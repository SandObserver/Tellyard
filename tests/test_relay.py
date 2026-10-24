import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from tellyard import relay as relay_module
from tellyard.relay import Feed, PlaylistError, Relay, rewrite
from tellyard.resolve import Resolved

SEGMENT = bytes(range(256)) * 4
MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.0,

seg1.ts
#EXTINF:2.0,
https://cdn.example.com/a/seg2.ts?token=x
"""


class Upstream(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, str]]] = []

    def do_GET(self) -> None:
        Upstream.seen.append(dict(self.headers.items()))
        port = self.connection.getsockname()[1]
        if self.path == "/live/media.m3u8":
            self._reply(200, MEDIA.encode(), "application/vnd.apple.mpegurl")
        elif self.path == "/live/elsewhere.m3u8":
            body = f"#EXTM3U\n#EXTINF:2.0,\nhttp://localhost:{port}/live/seg1.ts\n"
            self._reply(200, body.encode(), "application/vnd.apple.mpegurl")
        elif self.path == "/live/page.html":
            self._reply(200, b"<html></html>", "text/html")
        elif self.path == "/live/nolength.ts":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(SEGMENT)
        elif self.path == "/live/seg1.ts":
            span = self.headers.get("Range")
            if span:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes 0-9/{len(SEGMENT)}")
                self.send_header("Content-Length", "10")
                self.end_headers()
                self.wfile.write(SEGMENT[:10])
            else:
                self._reply(200, SEGMENT, "video/mp2t")
        else:
            self._reply(404, b"", "text/plain")

    def _reply(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def upstream() -> Iterator[str]:
    Upstream.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def feed_for(relay: Relay, url: str) -> Feed:
    headers = (("Referer", "https://tv.example.com/"), ("Cookie", "c=1"))
    return relay.open(Resolved(url, None, 1080, headers, frozenset({"127.0.0.1"})), "7 Seven")


def get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def test_rewrite_sends_every_address_through_the_relay() -> None:
    seen: list[tuple[str, bool]] = []

    def address(url: str, playlist: bool) -> str:
        seen.append((url, playlist))
        return f"http://relay/{len(seen)}"

    master = (
        '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio/en.m3u8"\n'
        "#EXT-X-STREAM-INF:BANDWIDTH=1\nvideo/1080.m3u8\n"
    )
    assert rewrite(master, "https://cdn.example.com/live/master.m3u8", address).splitlines() == [
        "#EXTM3U",
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="http://relay/1"',
        "#EXT-X-STREAM-INF:BANDWIDTH=1",
        "http://relay/2",
    ]
    assert seen == [
        ("https://cdn.example.com/live/audio/en.m3u8", True),
        ("https://cdn.example.com/live/video/1080.m3u8", True),
    ]
    seen.clear()
    rewrite(MEDIA, "https://cdn.example.com/live/media.m3u8", address)
    assert seen == [
        ("https://cdn.example.com/live/key.bin", False),
        ("https://cdn.example.com/live/init.mp4", False),
        ("https://cdn.example.com/live/seg1.ts", False),
        ("https://cdn.example.com/a/seg2.ts?token=x", False),
    ]


def test_rewrite_sends_unquoted_addresses_through_the_relay() -> None:
    text = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=http://192.168.1.1/k.bin,IV=0x1\n#EXT-X-MAP:URI=init.mp4\ns1.ts\n"
    assert rewrite(text, "https://cdn.example.com/live/media.m3u8", lambda url, playlist: f"r/{url}").splitlines() == [
        "#EXTM3U",
        '#EXT-X-KEY:METHOD=AES-128,URI="r/http://192.168.1.1/k.bin",IV=0x1',
        '#EXT-X-MAP:URI="r/https://cdn.example.com/live/init.mp4"',
        "r/https://cdn.example.com/live/s1.ts",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "file:///etc/passwd",
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key"',
        "#EXT-X-KEY:METHOD=AES-128,URI=file:///etc/passwd",
        "data:,x",
    ],
)
def test_rewrite_refuses_other_schemes(line: str) -> None:
    with pytest.raises(PlaylistError, match="the playlist has a"):
        rewrite(f"#EXTM3U\n{line}\n", "https://cdn.example.com/live/media.m3u8", lambda url, playlist: url)


def test_feed_keeps_the_upstream_file_name(relay: Relay) -> None:
    feed = feed_for(relay, "https://cdn.example.com/live/media.m3u8?sig=1")
    assert feed.video.startswith(f"{relay.base}/")
    assert feed.video.endswith("/0/media.m3u8")
    assert feed.address("https://cdn.example.com/a/seg%20one.ts?t=1", False).endswith("/seg%20one.ts")
    assert feed.address("https://cdn.example.com/", False).endswith("/segment")
    assert feed.address("https://cdn.example.com/a/seg%20one.ts?t=1", False).endswith("/1/seg%20one.ts")
    assert feed.audio is None


def test_feed_forgets_old_addresses(relay: Relay, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay_module, "ENTRIES", 3)
    feed = feed_for(relay, "https://cdn.example.com/live/media.m3u8")
    for number in range(5):
        feed.address(f"https://cdn.example.com/s{number}.ts", False)
    assert feed.entry(0) is None
    assert feed.entry(5) == ("https://cdn.example.com/s4.ts", False)
    assert feed.address("https://cdn.example.com/s4.ts", False).endswith("/5/s4.ts")


def test_relay_serves_rewritten_playlists_and_segments(relay: Relay, upstream: str) -> None:
    feed = feed_for(relay, f"{upstream}/live/media.m3u8")
    status, headers, body = get(feed.video)
    assert status == 200
    lines = body.decode().splitlines()
    assert all(line.startswith(f"{relay.base}/") for line in lines if line and not line.startswith("#"))
    assert upstream not in body.decode()
    segment = next(line for line in lines if line.endswith("/seg1.ts"))
    status, headers, body = get(segment)
    assert (status, headers["Content-Type"], body) == (200, "video/mp2t", SEGMENT)
    status, headers, body = get(segment, {"Range": "bytes=0-9"})
    assert (status, headers["Content-Range"], body) == (206, f"bytes 0-9/{len(SEGMENT)}", SEGMENT[:10])
    assert Upstream.seen[-1]["Range"] == "bytes=0-9"
    assert all(seen["Referer"] == "https://tv.example.com/" for seen in Upstream.seen)
    assert all(seen["Cookie"] == "c=1" for seen in Upstream.seen)


def test_relay_refuses_private_addresses_the_owner_did_not_name(
    relay: Relay, upstream: str, caplog: pytest.LogCaptureFixture
) -> None:
    feed = feed_for(relay, f"{upstream}/live/elsewhere.m3u8")
    body = get(feed.video)[2].decode()
    segment = body.splitlines()[-1]
    with caplog.at_level(logging.WARNING):
        assert get(segment)[0] == 502
    reasons = [getattr(record, "reason", "") for record in caplog.records]
    assert any("private addresses need a host named" in reason for reason in reasons)
    assert len(Upstream.seen) == 1


def test_relay_answers_errors(relay: Relay, upstream: str) -> None:
    assert get(f"{relay.base}/nothing")[0] == 404
    assert get(f"{relay.base}/{'a' * 22}/0/x.m3u8")[0] == 404
    assert get(feed_for(relay, f"{upstream}/live/page.html").video)[0] == 502
    assert get(feed_for(relay, f"{upstream}/live/missing.m3u8").video)[0] == 502
    feed = feed_for(relay, f"{upstream}/live/media.m3u8")
    feed.close()
    assert get(feed.video)[0] == 404


def test_relay_marks_the_end_of_a_body_without_length(relay: Relay, upstream: str) -> None:
    feed = feed_for(relay, f"{upstream}/live/media.m3u8")
    status, headers, body = get(feed.address(f"{upstream}/live/nolength.ts", False))
    assert (status, headers["Transfer-Encoding"], body) == (200, "chunked", SEGMENT)


def live(segments: int, extra: str = "") -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:5", "#EXT-X-MEDIA-SEQUENCE:100"]
    lines += ['#EXT-X-KEY:METHOD=AES-128,URI="k1"', '#EXT-X-MAP:URI="init.mp4"', extra]
    for number in range(segments):
        if number == 5:
            lines += ["#EXT-X-DISCONTINUITY", '#EXT-X-KEY:METHOD=AES-128,URI="k2"']
        lines += [f"#EXT-X-PROGRAM-DATE-TIME:2026-10-23T12:00:{number:02d}Z", "#EXTINF:5.0,", f"s{100 + number}.ts"]
    return "\n".join(line for line in lines if line) + "\n"


def test_live_edge_keeps_the_newest_segments() -> None:
    lines = relay_module.live_edge(live(40)).splitlines()
    uris = [line for line in lines if not line.startswith("#")]
    assert uris == [f"s{number}.ts" for number in range(110, 140)]
    assert "#EXT-X-MEDIA-SEQUENCE:110" in lines
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:1" in lines
    assert '#EXT-X-KEY:METHOD=AES-128,URI="k2"' in lines[: lines.index("s110.ts")]
    assert '#EXT-X-MAP:URI="init.mp4"' in lines
    assert "#EXT-X-DISCONTINUITY" not in lines
    assert lines[:3] == ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:5"]
    assert lines[lines.index("s110.ts") - 1] == "#EXTINF:5.0,"


@pytest.mark.parametrize("text", [live(30), live(40, "#EXT-X-ENDLIST"), live(40, "#EXT-X-BYTERANGE:100@0")])
def test_live_edge_leaves_other_playlists_alone(text: str) -> None:
    assert relay_module.live_edge(text) == text
