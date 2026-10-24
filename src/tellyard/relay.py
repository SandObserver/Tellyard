import logging
import re
import secrets
import threading
import urllib.parse
from collections import OrderedDict
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tellyard import net
from tellyard.channels import Headers
from tellyard.net import FetchError, Hosts
from tellyard.resolve import Resolved

log = logging.getLogger(__name__)
PLAYLIST_DEADLINE = 15.0
SEGMENT_DEADLINE = 60.0
SOCKET_TIMEOUT = 30.0
ENTRIES = 1024
LIVE_SEGMENTS = 30
CHUNK = 64 * 1024
NAME_LENGTH = 100
PATH = re.compile(r"^/([A-Za-z0-9_-]{22})/(\d{1,9})/[^/]*$")
# ffmpeg also follows unquoted values, so both forms go through the relay.
URI_ATTRIBUTE = re.compile(r'(?<![\w-])URI=(?:"([^"]*)"|([^",\s]*))')
VARIANT_TAG = "#EXT-X-STREAM-INF"
PLAYLIST_TAGS = ("#EXT-X-MEDIA:", "#EXT-X-I-FRAME-STREAM-INF:", "#EXT-X-RENDITION-REPORT:")
PASSED_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
SEQUENCE_TAG = "#EXT-X-MEDIA-SEQUENCE:"
DISCONTINUITY_SEQUENCE_TAG = "#EXT-X-DISCONTINUITY-SEQUENCE:"
HEADER_TAGS = (
    "#EXTM3U", "#EXT-X-VERSION:", "#EXT-X-TARGETDURATION:", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-START:",
    "#EXT-X-SERVER-CONTROL:", "#EXT-X-PART-INF:", "#EXT-X-ALLOW-CACHE:", "#EXT-X-DEFINE:",
)  # fmt: skip
STATE_TAGS = ("#EXT-X-KEY:", "#EXT-X-MAP:")

type Address = Callable[[str, bool], str]


class PlaylistError(Exception):
    pass


def rewrite(text: str, base: str, address: Address) -> str:
    lines: list[str] = []
    variant = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("#"):
            lines.append(address(_join(base, line), variant))
            variant = False
            continue
        if "URI=" in line:
            line = _rewrite_attribute(line, base, address)
        if line.startswith(VARIANT_TAG):
            variant = True
        lines.append(line)
    return "\n".join(lines) + "\n"


def live_edge(text: str) -> str:
    # Live playlists can list an hour of segments. ffmpeg plays from the newest few, so only those are passed on.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if "#EXT-X-ENDLIST" in lines or any(line.startswith(("#EXT-X-BYTERANGE", VARIANT_TAG)) for line in lines):
        return text
    uris = [index for index, line in enumerate(lines) if not line.startswith("#")]
    dropped = len(uris) - LIVE_SEGMENTS
    if dropped <= 0:
        return text
    cut = uris[dropped - 1] + 1
    head = [line for line in lines[:cut] if line.startswith(HEADER_TAGS)]
    state = {tag: line for line in lines[:cut] for tag in STATE_TAGS if line.startswith(tag)}
    sequence = _number(lines[:cut], SEQUENCE_TAG) + dropped
    discontinuities = sum(line == "#EXT-X-DISCONTINUITY" for line in lines[:cut])
    head.append(f"{SEQUENCE_TAG}{sequence}")
    if discontinuities or any(line.startswith(DISCONTINUITY_SEQUENCE_TAG) for line in lines):
        head.append(f"{DISCONTINUITY_SEQUENCE_TAG}{_number(lines[:cut], DISCONTINUITY_SEQUENCE_TAG) + discontinuities}")
    return "\n".join([*head, *state.values(), *lines[cut:]]) + "\n"


def _number(lines: list[str], tag: str) -> int:
    value = next((line.removeprefix(tag) for line in lines if line.startswith(tag)), "0")
    return int(value) if value.isascii() and value.isdigit() else 0


def _rewrite_attribute(line: str, base: str, address: Address) -> str:
    playlist = line.startswith(PLAYLIST_TAGS)

    def replace(found: re.Match[str]) -> str:
        reference = found[1] if found[1] is not None else found[2]
        return f'URI="{address(_join(base, reference), playlist)}"'

    return URI_ATTRIBUTE.sub(replace, line)


def _join(base: str, reference: str) -> str:
    uri = urllib.parse.urljoin(base, reference)
    scheme = urllib.parse.urlsplit(uri).scheme.lower()
    if scheme not in net.SCHEMES:
        raise PlaylistError(f"the playlist has a {scheme or 'relative'} address")
    return uri


def _name(url: str, playlist: bool) -> str:
    # ffmpeg accepts a segment only when its file name has a known media extension. Keep the upstream name.
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rpartition("/")[2])[-NAME_LENGTH:]
    return urllib.parse.quote(name, safe="._-~") or ("index.m3u8" if playlist else "segment")


class Feed:
    def __init__(self, relay: "Relay", token: str, resolved: Resolved, label: str) -> None:
        self._relay = relay
        self._token = token
        self._lock = threading.Lock()
        self._entries: OrderedDict[int, tuple[str, bool]] = OrderedDict()
        self._ids: dict[str, int] = {}
        self._next = 0
        self.headers: Headers = resolved.headers
        self.trusted: Hosts = resolved.trusted
        self.label = label
        self.closed = threading.Event()
        self.video = self.address(resolved.video, True)
        self.audio = self.address(resolved.audio, True) if resolved.audio else None

    def address(self, url: str, playlist: bool) -> str:
        with self._lock:
            number = self._ids.get(url)
            if number is None:
                number, self._next = self._next, self._next + 1
                self._ids[url] = number
                self._entries[number] = (url, playlist)
                while len(self._entries) > ENTRIES:
                    _, (old, _) = self._entries.popitem(last=False)
                    del self._ids[old]
            else:
                self._entries.move_to_end(number)
        return f"{self._relay.base}/{self._token}/{number}/{_name(url, playlist)}"

    def entry(self, number: int) -> tuple[str, bool] | None:
        with self._lock:
            return self._entries.get(number)

    def close(self) -> None:
        self.closed.set()
        self._relay.forget(self._token)


class Relay:
    def __init__(self) -> None:
        self._feeds: dict[str, Feed] = {}
        self._lock = threading.Lock()
        self._server = _Server(self)
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, name="relay", daemon=True).start()

    def open(self, resolved: Resolved, label: str) -> Feed:
        token = secrets.token_urlsafe(16)
        feed = Feed(self, token, resolved, label)
        with self._lock:
            self._feeds[token] = feed
        return feed

    def feed(self, token: str) -> Feed | None:
        with self._lock:
            return self._feeds.get(token)

    def forget(self, token: str) -> None:
        with self._lock:
            self._feeds.pop(token, None)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, relay: Relay) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.relay = relay


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"
    timeout = SOCKET_TIMEOUT

    def do_GET(self) -> None:
        found = PATH.match(urllib.parse.urlsplit(self.path).path)
        feed = self.server.relay.feed(found.group(1)) if found else None
        entry = feed.entry(int(found.group(2))) if feed and found else None
        if feed is None or entry is None:
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
            return
        url, playlist = entry
        if playlist:
            self._playlist(feed, url)
        else:
            self._segment(feed, url)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _playlist(self, feed: Feed, url: str) -> None:
        try:
            final, text = net.fetch_text(url, feed.headers, feed.trusted, deadline=PLAYLIST_DEADLINE)
            if not text.lstrip().startswith("#EXTM3U"):
                raise PlaylistError("the stream URL did not return an HLS playlist")
            body = rewrite(live_edge(text), final, feed.address).encode()
        except (FetchError, PlaylistError) as error:
            self._fail(feed, error)
            return
        self._send(HTTPStatus.OK, "application/vnd.apple.mpegurl", body)

    def _segment(self, feed: Feed, url: str) -> None:
        headers = feed.headers
        if span := self.headers.get("Range"):
            headers = (*headers, ("Range", span))
        started = False
        try:
            with net.open_url(url, headers, feed.trusted, deadline=SEGMENT_DEADLINE) as response:
                # ffmpeg logs a body without a length as cut short. Chunked encoding marks the end.
                chunked = response.headers.get("Content-Length") is None
                self.send_response(response.status)
                for name in PASSED_HEADERS:
                    if value := response.headers.get(name):
                        self.send_header(name, value)
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                started = True
                while not feed.closed.is_set() and (chunk := net.read_block(response, CHUNK)):
                    if not self._write(b"%x\r\n%b\r\n" % (len(chunk), chunk) if chunked else chunk):
                        return
                if chunked and not feed.closed.is_set():
                    self._write(b"0\r\n\r\n")
        except FetchError as error:
            if not started:
                self._fail(feed, error)

    def _write(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
        except OSError:
            return False
        return True

    def _fail(self, feed: Feed, error: Exception) -> None:
        if not feed.closed.is_set():
            log.warning("source request failed", extra={"channel": feed.label, "reason": str(error)})
        self._send(HTTPStatus.BAD_GATEWAY, "text/plain; charset=utf-8", b"source request failed\n")

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
