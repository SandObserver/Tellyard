import gzip
import http.client
import socket
import threading
import time
import tracemalloc
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tellyard import net
from tellyard.net import FetchError

REDIRECTS = {"/redirect": "/page", "/to-ftp": "ftp://127.0.0.1:1/x.m3u8", "/to-localhost": "http://localhost:PORT/page"}
PAGES = {"/page": b"<html>hello</html>", "/guide.xml.gz": gzip.compress(b"<tv/>"), "/big": b"x" * 2048}
PIECES = 100_000
TINY_CHUNKS = b"1\r\n#\r\n" * PIECES + b"0\r\n\r\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path].replace("PORT", str(self.connection.getsockname()[1])))
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
        if self.path == "/pieces":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(TINY_CHUNKS)
            return
        body = self.path.encode() if self.path.startswith("/echo/") else PAGES.get(self.path)
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


LOCAL = frozenset({"127.0.0.1"})


@pytest.fixture
def base() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_text_follows_redirects(base: str) -> None:
    final, text = net.fetch_text(f"{base}/redirect", (("Referer", "https://r.example.com/"),), LOCAL)
    assert final == f"{base}/page"
    assert text == "<html>hello</html>"


def test_fetch_text_keeps_memory_low_for_tiny_chunks(base: str) -> None:
    tracemalloc.start()
    try:
        _, text = net.fetch_text(f"{base}/pieces", trusted=LOCAL)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert text == "#" * PIECES
    assert peak < 30 * PIECES


def test_request_sends_a_browser_agent_and_headers() -> None:
    request = net.request("https://a.example.com/", (("Referer", "https://r.example.com/"),))
    assert "Mozilla" in (request.get_header("User-agent") or "")
    assert request.get_header("Referer") == "https://r.example.com/"


def test_fetch_text_errors(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(FetchError, match="HTTP 404"):
        net.fetch_text(f"{base}/missing", trusted=LOCAL)
    monkeypatch.setattr(net, "TEXT_LIMIT", 1024)
    with pytest.raises(FetchError, match="larger than"):
        net.fetch_text(f"{base}/big", trusted=LOCAL)
    with pytest.raises(FetchError):
        net.fetch_text("http://127.0.0.1:1/", trusted=LOCAL)


def test_download_and_open_gzip(base: str, tmp_path: Path) -> None:
    target = tmp_path / "guide.xml"
    net.download(f"{base}/guide.xml.gz", target, LOCAL)
    with net.open_maybe_gzip(target) as stream:
        assert stream.read() == b"<tv/>"
    plain = tmp_path / "plain.xml"
    plain.write_bytes(b"<tv/>")
    with net.open_maybe_gzip(plain) as stream:
        assert stream.read() == b"<tv/>"


def test_download_errors_leave_no_partial_file(base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "guide.xml"
    with pytest.raises(FetchError, match="HTTP 404"):
        net.download(f"{base}/missing", target, LOCAL)
    monkeypatch.setattr(net, "DOWNLOAD_LIMIT", 1024)
    monkeypatch.setattr(net, "CHUNK", 512)
    with pytest.raises(FetchError, match="larger than"):
        net.download(f"{base}/big", target, LOCAL)
    assert list(tmp_path.iterdir()) == []


def test_fetch_text_stops_a_slow_response(base: str) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="took longer than 1 s"):
        net.fetch_text(f"{base}/slow", trusted=LOCAL, deadline=1.0)
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
        net.fetch_text(f"http://127.0.0.1:{listener.getsockname()[1]}/", trusted=LOCAL, deadline=1.0)
    assert time.monotonic() - started < 3


def test_private_addresses_need_a_named_host(base: str) -> None:
    with pytest.raises(FetchError, match=r"refused 127\.0\.0\.1"):
        net.fetch_text(f"{base}/page")
    with pytest.raises(FetchError, match=r"refused .*private"):
        net.fetch_text(f"{base}/to-localhost", trusted=LOCAL)


def test_redirects_to_other_schemes_are_refused(base: str) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="refused a redirect to a ftp address"):
        net.fetch_text(f"{base}/to-ftp", trusted=LOCAL)
    assert time.monotonic() - started < 2
    with pytest.raises(FetchError, match="only http and https"):
        net.fetch_text("ftp://127.0.0.1:1/x.m3u8", trusted=LOCAL)


def test_credentials_go_only_to_named_hosts() -> None:
    headers = (("Authorization", "Bearer x"), ("Cookie", "c=1"), ("Referer", "https://r.example.com/"))
    named = net.request("https://tv.example.com/a", headers, frozenset({"tv.example.com"}))
    other = net.request("https://cdn.example.com/a", headers, frozenset({"tv.example.com"}))
    assert named.get_header("Authorization") == "Bearer x"
    assert named.get_header("Cookie") == "c=1"
    assert other.get_header("Authorization") is None
    assert other.get_header("Cookie") is None
    assert other.get_header("Referer") == "https://r.example.com/"
    redirects = net._Redirects(frozenset({"tv.example.com"}))
    moved = redirects.redirect_request(named, None, 302, "Found", {}, "https://cdn.example.com/b")
    assert moved is not None
    assert moved.get_header("Authorization") is None
    assert moved.get_header("Cookie") is None
    assert moved.get_header("Referer") == "https://r.example.com/"
    stayed = redirects.redirect_request(named, None, 302, "Found", {}, "https://tv.example.com/b")
    assert stayed is not None
    assert stayed.get_header("Authorization") == "Bearer x"


def test_download_has_a_total_deadline(base: str, tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="took longer than 1 s"):
        net.download(f"{base}/slow", tmp_path / "guide.xml", LOCAL, deadline=1.0)
    assert time.monotonic() - started < 2
    assert list(tmp_path.iterdir()) == []


def test_errors_and_logs_show_only_the_host() -> None:
    with pytest.raises(FetchError) as caught:
        net.fetch_text("http://user:secret@127.0.0.1:1/live?token=abc", trusted=LOCAL)
    assert str(caught.value).startswith("127.0.0.1:1: ")
    assert "secret" not in str(caught.value)
    assert "abc" not in str(caught.value)
    assert net.safe_url("https://u:p@[::1]:8443/x?y=z") == "[::1]:8443"
    assert net.safe_url("http://[bad") == "an invalid address"
    assert net.redact("open https://u:p@cdn.example.com/a/b?t=1 failed") == "open https://cdn.example.com failed"


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("192.168.1.2", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fd00::1", False),
        ("fe80::1%en0", False),
        ("::ffff:10.1.2.3", False),
        ("224.0.0.1", False),
        ("not an address", False),
    ],
)
def test_is_public(address: str, public: bool) -> None:
    assert net.is_public(address) is public


def test_https_requests_share_one_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    contexts: list[object] = []

    def do_open(self: object, connection: object, req: object, **kwargs: object) -> None:
        contexts.append(kwargs.get("context"))
        raise OSError("stop")

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, "do_open", do_open)
    for _ in range(2):
        with pytest.raises(FetchError):
            net.fetch_text("https://v.example.com/a.m3u8")
    assert contexts == [net.TLS, net.TLS]


def test_unusual_characters_in_addresses_are_encoded(base: str) -> None:
    _, text = net.fetch_text(f"{base}/echo/seg 1-\u062e\u0628\u0631.ts?q=a b&t=%41", trusted=LOCAL)
    assert text == "/echo/seg%201-%D8%AE%D8%A8%D8%B1.ts?q=a%20b&t=%41"


def test_invalid_addresses_are_not_repeated_in_errors() -> None:
    with pytest.raises(FetchError) as caught:
        net.fetch_text("http://[::1/live/seg.ts?sig=SECRET", trusted=LOCAL)
    assert "SECRET" not in str(caught.value)
    assert str(caught.value).endswith("the address has characters that are not allowed")
    invalid = http.client.InvalidURL("URL can't contain control characters. '/live/seg 1.ts?sig=SECRET'")
    assert net._reason(invalid) == "the address has characters that are not allowed"


def test_private_addresses_are_refused_behind_a_proxy(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", base)
    monkeypatch.delenv("no_proxy", raising=False)
    with pytest.raises(FetchError, match=r"refused 192\.168\.1\.1: private addresses need a host named"):
        net.fetch_text("http://192.168.1.1/page", trusted=frozenset({"cdn.example.com"}))
    with pytest.raises(FetchError, match="HTTP 404"):
        net.fetch_text("http://192.168.1.1/page", trusted=frozenset({"192.168.1.1"}))
