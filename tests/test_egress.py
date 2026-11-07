import json
import logging
import socket
import struct
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from tellyard import egress, net
from tellyard.net import Trust

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg.ts\n"


class Site(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[int, str]]] = []
    other = 0

    def do_GET(self) -> None:
        Site.seen.append((self.connection.getsockname()[1], self.path))
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{Site.other}/private.m3u8")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(PLAYLIST)))
        self.end_headers()
        self.wfile.write(PLAYLIST)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    Site.seen = []
    servers = [ThreadingHTTPServer(("127.0.0.1", 0), Site) for _ in range(2)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    Site.other = servers[1].server_address[1]
    yield tuple(f"http://127.0.0.1:{server.server_address[1]}" for server in servers)  # type: ignore[misc]
    for server in servers:
        server.shutdown()
        server.server_close()


def socks(proxy: str, request: bytes, greeting: bytes = b"\x05\x01\x00") -> tuple[bytes, socket.socket]:
    host, port = proxy.removeprefix("socks5h://").split(":")
    sock = socket.create_connection((host, int(port)), timeout=5)
    sock.sendall(greeting)
    answer = sock.recv(2)
    if answer != b"\x05\x00":
        return answer, sock
    sock.sendall(request)
    return sock.recv(10), sock


def domain(host: str, port: int) -> bytes:
    return b"\x05\x01\x00\x03" + bytes([len(host)]) + host.encode() + struct.pack(">H", port)


def test_tunnels_to_a_named_origin(sites: tuple[str, str]) -> None:
    named, _ = sites
    port = int(named.rpartition(":")[2])
    with egress.serve(net.trust_of([named]), "9") as proxy:
        reply, sock = socks(proxy, domain("127.0.0.1", port))
        with sock:
            assert reply[:2] == b"\x05\x00"
            sock.sendall(b"GET /a.m3u8 HTTP/1.0\r\nHost: x\r\n\r\n")
            assert b"#EXTM3U" in sock.recv(4096) + sock.recv(4096)
    assert Site.seen == [(port, "/a.m3u8")]


def test_refuses_private_addresses_the_channel_does_not_name(
    sites: tuple[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    named, other = sites
    request = b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", int(other.rpartition(":")[2]))
    with caplog.at_level(logging.WARNING), egress.serve(net.trust_of([named]), "9") as proxy:
        reply, sock = socks(proxy, request)
        sock.close()
    assert reply[:2] == b"\x05\x02"
    assert Site.seen == []
    assert any("private addresses need a host and port named" in getattr(r, "reason", "") for r in caplog.records)


@pytest.mark.parametrize(
    ("greeting", "ask", "answer"),
    [
        (b"\x04\x01\x00", b"", b"\x05\xff"),
        (b"\x05\x01\x02", b"", b"\x05\xff"),
        (b"\x05\x01\x00", b"\x05\x02\x00\x03\x01a\x00\x50", b"\x05\x07"),
        (b"\x05\x01\x00", b"\x05\x01\x00\x09", b"\x05\x08"),
        (b"\x05\x01\x00", domain("a\r\nb", 80), b"\x05\x08"),
        (b"\x05\x01\x00", domain("example.com", 0), b"\x05\x08"),
    ],
)
def test_answers_bad_requests(greeting: bytes, ask: bytes, answer: bytes) -> None:
    with egress.serve(Trust(), "9") as proxy:
        reply, sock = socks(proxy, ask, greeting)
        sock.close()
    assert reply[:2] == answer


def test_ipv6_targets_are_checked() -> None:
    request = b"\x05\x01\x00\x04" + socket.inet_pton(socket.AF_INET6, "::1") + struct.pack(">H", 80)
    with egress.serve(Trust(), "9") as proxy:
        reply, sock = socks(proxy, request)
        sock.close()
    assert reply[:2] == b"\x05\x02"


def test_an_unreachable_target_fails() -> None:
    closed = socket.create_server(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    with egress.serve(net.trust_of([f"http://127.0.0.1:{port}/"]), "9") as proxy:
        reply, sock = socks(proxy, domain("127.0.0.1", port))
        sock.close()
    assert reply[:2] == b"\x05\x01"


def test_goes_through_the_configured_proxy(sites: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    tunnels: list[bytes] = []
    listener = socket.create_server(("127.0.0.1", 0))

    def upstream() -> None:
        for answer in (b"HTTP/1.1 200 Connection established\r\n\r\n", b"HTTP/1.1 403 Forbidden\r\n\r\n"):
            connection, _ = listener.accept()
            with connection:
                head = b""
                while not head.endswith(b"\r\n\r\n"):
                    head += connection.recv(1)
                tunnels.append(head)
                connection.sendall(answer)
                if answer.split()[1] == b"200":
                    connection.sendall(connection.recv(100).upper())

    threading.Thread(target=upstream, daemon=True).start()
    monkeypatch.setenv("https_proxy", f"http://user:p%40ss@127.0.0.1:{listener.getsockname()[1]}")
    with listener, egress.serve(Trust(), "9") as proxy:
        reply, sock = socks(proxy, domain("cdn.example.com", 443))
        with sock:
            assert reply[:2] == b"\x05\x00"
            sock.sendall(b"hello")
            assert sock.recv(10) == b"HELLO"
        reply, sock = socks(proxy, domain("cdn.example.com", 443))
        sock.close()
        assert reply[:2] == b"\x05\x01"
    assert tunnels[0].startswith(b"CONNECT cdn.example.com:443 HTTP/1.1\r\n")
    assert b"Proxy-Authorization: Basic dXNlcjpwQHNz\r\n" in tunnels[0]


def test_behind_a_proxy_private_numbers_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    with pytest.raises(net.BlockedAddressError, match=r"refused 10\.0\.0\.1:443"):
        net.connect_out("10.1", 443, Trust(), 1)
    with pytest.raises(net.BlockedAddressError, match="not a valid address"):
        net.connect_out("256.1.1.1", 443, Trust(), 1)
    monkeypatch.setenv("https_proxy", "https://127.0.0.1:9")
    with pytest.raises(OSError, match="only http proxies"):
        net.connect_out("cdn.example.com", 443, Trust(), 1)


def test_ytdlp_cannot_follow_a_redirect_to_a_private_address(sites: tuple[str, str]) -> None:
    named, _ = sites
    with egress.serve(net.trust_of([named]), "9") as proxy:
        direct = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download", "-J", "--proxy", proxy, f"{named}/live.m3u8"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        moved = subprocess.run(
            ["yt-dlp", "--no-warnings", "--skip-download", "-J", "--proxy", proxy, f"{named}/moved"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert direct.returncode == 0
    assert json.loads(direct.stdout)["formats"]
    assert moved.returncode != 0
    assert "not allowed by ruleset" in moved.stderr
    assert all(port != Site.other for port, _ in Site.seen)
