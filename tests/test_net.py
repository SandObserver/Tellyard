import gzip
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tellyard import net
from tellyard.net import FetchError

PAGES = {"/page": b"<html>hello</html>", "/guide.xml.gz": gzip.compress(b"<tv/>"), "/big": b"x" * 2048}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
            return
        if self.path == "/slow":
            self.send_response(200)
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.2)
            return
        body = PAGES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Agent", self.headers.get("User-Agent", ""))
        self.send_header("X-Referer", self.headers.get("Referer", ""))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def base() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_text_follows_redirects(base: str) -> None:
    final, text = net.fetch_text(f"{base}/redirect", (("Referer", "https://r.example.com/"),))
    assert final == f"{base}/page"
    assert text == "<html>hello</html>"


def test_request_sends_a_browser_agent_and_headers() -> None:
    request = net.request("https://a.example.com/", (("Referer", "https://r.example.com/"),))
    assert "Mozilla" in (request.get_header("User-agent") or "")
    assert request.get_header("Referer") == "https://r.example.com/"


def test_fetch_text_errors(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(FetchError, match="HTTP 404"):
        net.fetch_text(f"{base}/missing")
    monkeypatch.setattr(net, "TEXT_LIMIT", 1024)
    with pytest.raises(FetchError, match="larger than"):
        net.fetch_text(f"{base}/big")
    with pytest.raises(FetchError):
        net.fetch_text("http://127.0.0.1:1/")


def test_download_and_open_gzip(base: str, tmp_path: Path) -> None:
    target = tmp_path / "guide.xml"
    net.download(f"{base}/guide.xml.gz", target)
    with net.open_maybe_gzip(target) as stream:
        assert stream.read() == b"<tv/>"
    plain = tmp_path / "plain.xml"
    plain.write_bytes(b"<tv/>")
    with net.open_maybe_gzip(plain) as stream:
        assert stream.read() == b"<tv/>"


def test_download_errors_leave_no_partial_file(base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "guide.xml"
    with pytest.raises(FetchError, match="HTTP 404"):
        net.download(f"{base}/missing", target)
    monkeypatch.setattr(net, "DOWNLOAD_LIMIT", 1024)
    monkeypatch.setattr(net, "CHUNK", 512)
    with pytest.raises(FetchError, match="larger than"):
        net.download(f"{base}/big", target)
    assert list(tmp_path.iterdir()) == []


def test_fetch_text_stops_a_slow_response(base: str) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="took longer than 1 s"):
        net.fetch_text(f"{base}/slow", deadline=1.0)
    assert time.monotonic() - started < 2


def test_fetch_text_stops_slow_headers() -> None:
    listener = socket.create_server(("127.0.0.1", 0))

    def drip() -> None:
        connection, _ = listener.accept()
        with connection:
            for byte in b"HTTP/1.0 200 OK\r\nX-Slow: " + b"x" * 100:
                try:
                    connection.sendall(bytes([byte]))
                except OSError:
                    return
                time.sleep(0.1)

    threading.Thread(target=drip, daemon=True).start()
    started = time.monotonic()
    with listener, pytest.raises(FetchError, match="took longer than 1 s"):
        net.fetch_text(f"http://127.0.0.1:{listener.getsockname()[1]}/", deadline=1.0)
    assert time.monotonic() - started < 3
