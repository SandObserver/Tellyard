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


@pytest.fixture
def base() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_text_follows_redirects(base: str) -> None:
    final, text = net.fetch_text(f"{base}/redirect", (("Referer", "https://r.example.com/"),), net.trust_of([base]))
    assert final == f"{base}/page"
    assert text == "<html>hello</html>"


def test_fetch_text_keeps_memory_low_for_tiny_chunks(base: str) -> None:
    tracemalloc.start()
    try:
        _, text = net.fetch_text(f"{base}/pieces", trust=net.trust_of([base]))
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
        net.fetch_text(f"{base}/missing", trust=net.trust_of([base]))
    monkeypatch.setattr(net, "TEXT_LIMIT", 1024)
    with pytest.raises(FetchError, match="larger than"):
        net.fetch_text(f"{base}/big", trust=net.trust_of([base]))
    with pytest.raises(FetchError):
        net.fetch_text("http://127.0.0.1:1/", trust=net.trust_of(["http://127.0.0.1:1/"]))


def test_download_and_open_gzip(base: str, tmp_path: Path) -> None:
    target = tmp_path / "guide.xml"
    net.download(f"{base}/guide.xml.gz", target, net.trust_of([base]))
    with net.open_maybe_gzip(target) as stream:
        assert stream.read() == b"<tv/>"
    plain = tmp_path / "plain.xml"
    plain.write_bytes(b"<tv/>")
    with net.open_maybe_gzip(plain) as stream:
        assert stream.read() == b"<tv/>"


def test_download_errors_leave_no_partial_file(base: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "guide.xml"
    with pytest.raises(FetchError, match="HTTP 404"):
        net.download(f"{base}/missing", target, net.trust_of([base]))
    monkeypatch.setattr(net, "DOWNLOAD_LIMIT", 1024)
    monkeypatch.setattr(net, "CHUNK", 512)
    with pytest.raises(FetchError, match="larger than"):
        net.download(f"{base}/big", target, net.trust_of([base]))
    assert list(tmp_path.iterdir()) == []


def test_fetch_text_stops_a_slow_response(base: str) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="took longer than 1 s"):
        net.fetch_text(f"{base}/slow", trust=net.trust_of([base]), deadline=1.0)
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
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/"
        net.fetch_text(url, trust=net.trust_of([url]), deadline=1.0)
    assert time.monotonic() - started < 3


def test_private_addresses_need_a_named_host(base: str) -> None:
    with pytest.raises(FetchError, match=r"refused 127\.0\.0\.1"):
        net.fetch_text(f"{base}/page")
    with pytest.raises(FetchError, match=r"refused .*private"):
        net.fetch_text(f"{base}/to-localhost", trust=net.trust_of([base]))


def test_redirects_to_other_schemes_are_refused(base: str) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="refused a redirect to a ftp address"):
        net.fetch_text(f"{base}/to-ftp", trust=net.trust_of([base]))
    assert time.monotonic() - started < 2
    with pytest.raises(FetchError, match="only http and https"):
        net.fetch_text("ftp://127.0.0.1:1/x.m3u8")


def test_credentials_go_only_to_the_named_origin() -> None:
    headers = (("Authorization", "Bearer x"), ("Cookie", "c=1"), ("Referer", "https://r.example.com/"))
    trust = net.trust_of(["https://tv.example.com/live.m3u8"])
    named = net.request("https://tv.example.com/a", headers, trust)
    assert named.get_header("Authorization") == "Bearer x"
    assert named.get_header("Cookie") == "c=1"
    for url in ("https://cdn.example.com/a", "http://tv.example.com/a", "https://tv.example.com:8443/a"):
        other = net.request(url, headers, trust)
        assert other.get_header("Authorization") is None
        assert other.get_header("Cookie") is None
        assert other.get_header("Referer") == "https://r.example.com/"
    redirects = net._Redirects(trust)
    moved = redirects.redirect_request(named, None, 302, "Found", {}, "http://tv.example.com/b")
    assert moved is not None
    assert moved.get_header("Authorization") is None
    stayed = redirects.redirect_request(named, None, 302, "Found", {}, "https://tv.example.com/b")
    assert stayed is not None
    assert stayed.get_header("Authorization") == "Bearer x"


def test_an_http_origin_also_trusts_its_https_upgrade() -> None:
    trust = net.trust_of(["http://tv.example.com/live.m3u8"])
    assert trust.sends_credentials("https://tv.example.com/a")
    assert trust.reaches("tv.example.com", 443)
    assert not trust.reaches("tv.example.com", 8080)
    assert not net.trust_of(["https://tv.example.com/"]).sends_credentials("http://tv.example.com/a")


def test_download_has_a_total_deadline(base: str, tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(FetchError, match="took longer than 1 s"):
        net.download(f"{base}/slow", tmp_path / "guide.xml", net.trust_of([base]), deadline=1.0)
    assert time.monotonic() - started < 2
    assert list(tmp_path.iterdir()) == []


def test_errors_and_logs_show_only_the_host() -> None:
    with pytest.raises(FetchError) as caught:
        net.fetch_text("http://user:secret@127.0.0.1:1/live?token=abc", trust=net.trust_of(["http://127.0.0.1:1/"]))
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
    _, text = net.fetch_text(f"{base}/echo/seg 1-\u062e\u0628\u0631.ts?q=a b&t=%41", trust=net.trust_of([base]))
    assert text == "/echo/seg%201-%D8%AE%D8%A8%D8%B1.ts?q=a%20b&t=%41"


def test_invalid_addresses_are_not_repeated_in_errors() -> None:
    with pytest.raises(FetchError) as caught:
        net.fetch_text("http://[::1/live/seg.ts?sig=SECRET")
    assert "SECRET" not in str(caught.value)
    assert str(caught.value).endswith("the address has characters that are not allowed")
    invalid = http.client.InvalidURL("URL can't contain control characters. '/live/seg 1.ts?sig=SECRET'")
    assert net._reason(invalid) == "the address has characters that are not allowed"


@pytest.mark.parametrize(
    ("host", "canonical"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("10.1", "10.0.0.1"),
        ("127.000.000.001", "127.0.0.1"),
        ("127.0.0.1.", "127.0.0.1"),
        ("0", "0.0.0.0"),
        ("0x", "0.0.0.0"),
        ("192.0xa8.1.1", "192.168.1.1"),
        ("::FFFF:7F00:1", "::ffff:127.0.0.1"),
        ("Tv.Example.com.", "tv.example.com"),
        ("1x.example.com", "1x.example.com"),
    ],
)
def test_numeric_hosts_are_read_like_a_browser(host: str, canonical: str) -> None:
    assert net.canonical_host(host) == canonical


@pytest.mark.parametrize("host", ["256.1.1.1", "1.2.3.4.5", "08", "1.2.3.4294967296", "a.0x10"])
def test_invalid_numeric_hosts_are_not_names(host: str) -> None:
    with pytest.raises(ValueError, match="not a valid IPv4"):
        net.canonical_host(host)


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            body = b"#EXTM3U\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    yield f"127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    "host", ["192.168.1.1", "2130706433", "0x7f000001", "127.1", "10.1", "0", "127.0.0.1.", "[::1]"]
)
def test_private_numbers_are_refused_behind_a_proxy(
    proxy: tuple[str, list[str]], monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    address, seen = proxy
    monkeypatch.setenv("http_proxy", f"http://{address}")
    with pytest.raises(FetchError, match=r"refused .*private addresses need a host and port named"):
        net.fetch_text(f"http://{host}/page", trust=net.trust_of(["https://cdn.example.com/"]))
    assert seen == []


def test_a_named_private_address_goes_through_the_proxy(
    proxy: tuple[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    address, seen = proxy
    monkeypatch.setenv("http_proxy", address)
    _, text = net.fetch_text("http://192.168.1.1/page", trust=net.trust_of(["http://192.168.1.1/"]))
    assert text == "#EXTM3U\n"
    assert seen == ["http://192.168.1.1/page"]
    _, text = net.fetch_text("http://cdn.example.com/a.m3u8")
    assert seen[-1] == "http://cdn.example.com/a.m3u8"


@pytest.mark.parametrize(
    "settings",
    [
        {"all_proxy": "socks5://PROXY"},
        {"https_proxy": "http://PROXY"},
        {"http_proxy": "http://PROXY", "no_proxy": "127.0.0.1"},
        {"ftp_proxy": "http://PROXY"},
    ],
)
def test_a_proxy_setting_does_not_trust_the_proxy_host(
    base: str, proxy: tuple[str, list[str]], monkeypatch: pytest.MonkeyPatch, settings: dict[str, str]
) -> None:
    for name, value in settings.items():
        monkeypatch.setenv(name, value.replace("PROXY", proxy[0]))
    with pytest.raises(FetchError, match=r"refused 127\.0\.0\.1:\d+: private"):
        net.fetch_text(f"{base}/page")


def test_trust_is_per_port(base: str) -> None:
    other = socket.create_server(("127.0.0.1", 0))
    with other, pytest.raises(FetchError, match=r"refused 127\.0\.0\.1:\d+: private"):
        net.fetch_text(f"http://127.0.0.1:{other.getsockname()[1]}/", trust=net.trust_of([base]))
    port = int(base.rpartition(":")[2])
    _, text = net.fetch_text(f"{base}/page", trust=net.trust_of(["http://example.com/"], [("127.0.0.1", port)]))
    assert text == "<html>hello</html>"


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("::127.0.0.1", False),
        ("64:ff9b::a00:1", False),
        ("64:ff9b::7f00:1", False),
        ("64:ff9b::5db8:d70e", True),
        ("2002:7f00:1::1", False),
        ("2001:0:4136:e378:8000:63bf:3fff:fdd2", False),
        ("168.63.129.16", False),
        ("fd00:ec2::254", False),
        ("100.100.100.200", False),
        ("0.0.0.0", False),
    ],
)
def test_embedded_and_metadata_addresses(address: str, public: bool) -> None:
    assert net.is_public(address) is public


def test_the_home_ipv6_network_is_not_public(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    table = tmp_path / "if_inet6"
    table.write_text(
        "2a0212345678ab000000000000000005 02 40 00 80     eth0\n"
        "fe800000000000000000000000000005 02 40 20 80     eth0\n"
        "00000000000000000000000000000001 01 80 10 80       lo\n"
        "broken line\n"
    )
    monkeypatch.setattr(net, "LOCAL", net._Interfaces(table))
    assert net.is_public("2a02:1234:5678:ab01::99")
    assert not net.is_public("2a02:1234:5678:ab00::99")
    assert not net.is_public("2a02:1234:5678:ab00:abcd::1")
    monkeypatch.setattr(net, "LOCAL", net._Interfaces(tmp_path / "missing"))
    assert net.LOCAL.networks() == ()
    assert net.is_public("2606:4700::1111")


def test_without_proxies() -> None:
    env = {"PATH": "/bin", "http_proxy": "a", "HTTPS_PROXY": "b", "all_proxy": "c", "no_proxy": "d"}
    assert net.without_proxies(env) == {"PATH": "/bin"}


def test_connects_to_the_checked_public_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in ("192.0.2.1", "1.1.1.1")]
    tried: list[tuple[str, int]] = []
    sentinel = socket.socket()

    def connect(address: tuple[str, int], *args: object) -> socket.socket:
        tried.append(address)
        if address[0] == "1.1.1.1":
            raise OSError("unreachable")
        return sentinel

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers[1:])
    monkeypatch.setattr(socket, "create_connection", connect)
    connector = net._connector(net.NOBODY, proxy=False)
    with pytest.raises(OSError, match="unreachable"):
        connector(("cdn.example.com", 443), 1.0, None)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: answers)
    with pytest.raises(net.BlockedAddressError, match=r"refused 192\.0\.2\.1:443"):
        connector(("cdn.example.com", 443), 1.0, None)
    assert tried == [("1.1.1.1", 443)]
    sentinel.close()


@pytest.mark.parametrize(
    "url", ["ftp://a.example.com/", "http://a.example.com:99999/", "http:///x", "http://256.1.1.1/"]
)
def test_origins_of_unusable_addresses(url: str) -> None:
    assert net.origin_of(url) is None
    assert net.trust_of([url], [("256.1.1.1", 80)]) == net.Trust()


def test_fetch_text_stops_when_cancelled(base: str) -> None:
    cancel = net.Cancel()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(FetchError, match="cancelled"):
        net.fetch_text(f"{base}/slow", trust=net.trust_of([base]), cancel=cancel)
    assert time.monotonic() - started < 2


def test_fetch_text_does_not_start_when_already_cancelled(base: str) -> None:
    cancel = net.Cancel()
    cancel.set()
    with pytest.raises(FetchError, match="cancelled"):
        net.fetch_text(f"{base}/page", trust=net.trust_of([base]), cancel=cancel)


def test_cancel_runs_hooks_only_while_they_are_registered() -> None:
    cancel = net.Cancel()
    calls: list[str] = []
    with cancel.on_set(lambda: calls.append("during")):
        pass
    with cancel.on_set(lambda: calls.append("set")):
        cancel.set()
    with cancel.on_set(lambda: calls.append("already set")):
        pass
    assert calls == ["set", "already set"]
