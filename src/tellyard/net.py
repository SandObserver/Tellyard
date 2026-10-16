import contextlib
import gzip
import http.client
import io
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tellyard.channels import Headers

# Some broadcasters block requests without a browser user agent.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TEXT_LIMIT = 4 * 1024 * 1024
TEXT_DEADLINE = 30.0
DOWNLOAD_LIMIT = 256 * 1024 * 1024
CHUNK = 256 * 1024
GZIP_MAGIC = b"\x1f\x8b"


class FetchError(Exception):
    pass


def request(url: str, headers: Headers = ()) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    merged.update(dict(headers))
    return urllib.request.Request(url, headers=merged)


def fetch_text(
    url: str, headers: Headers = (), timeout: float = 10.0, deadline: float = TEXT_DEADLINE
) -> tuple[str, str]:
    # The socket timeout applies to each read. The deadline closes the connection to bound the whole request.
    watch = _Deadline()
    opener = urllib.request.build_opener(_HttpHandler(watch), _HttpsHandler(watch))
    timer = threading.Timer(deadline, watch.expire)
    timer.start()
    body = bytearray()
    try:
        with opener.open(request(url, headers), timeout=timeout) as response:
            final = response.geturl()
            while len(body) <= TEXT_LIMIT and (chunk := response.read1(CHUNK)):
                body += chunk
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        if watch.expired:
            raise FetchError(f"{_host(url)}: response took longer than {deadline:.0f} s") from error
        raise FetchError(f"{_host(url)}: {_reason(error)}") from error
    finally:
        timer.cancel()
    if watch.expired:
        raise FetchError(f"{_host(url)}: response took longer than {deadline:.0f} s")
    if len(body) > TEXT_LIMIT:
        raise FetchError(f"{_host(url)}: response is larger than {TEXT_LIMIT // 1024 // 1024} MB")
    return final, body.decode("utf-8", errors="replace")


def download(url: str, destination: Path, timeout: float = 60.0) -> None:
    partial = destination.with_name(destination.name + ".part")
    total = 0
    try:
        with urllib.request.urlopen(request(url), timeout=timeout) as response, partial.open("wb") as out:
            while chunk := response.read(CHUNK):
                total += len(chunk)
                if total > DOWNLOAD_LIMIT:
                    raise FetchError(f"{_host(url)}: file is larger than {DOWNLOAD_LIMIT // 1024 // 1024} MB")
                out.write(chunk)
        partial.replace(destination)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{_host(url)}: {_reason(error)}") from error
    finally:
        partial.unlink(missing_ok=True)


class _Deadline:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self.expired = False

    def connection(self, base: type[http.client.HTTPConnection]) -> type[http.client.HTTPConnection]:
        watch = self

        class Watched(base):  # type: ignore[valid-type,misc]
            def connect(self) -> None:
                super().connect()
                watch.add(self.sock)

        return Watched

    def add(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.append(sock)
            if self.expired:
                _shut(sock)

    def expire(self) -> None:
        with self._lock:
            self.expired = True
            for sock in self._sockets:
                _shut(sock)


class _HttpHandler(urllib.request.HTTPHandler):
    def __init__(self, watch: _Deadline) -> None:
        super().__init__()
        self._watch = watch

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._watch.connection(http.client.HTTPConnection), req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, watch: _Deadline) -> None:
        super().__init__()
        self._watch = watch

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._watch.connection(http.client.HTTPSConnection), req)


def _shut(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def open_maybe_gzip(path: Path) -> io.BufferedIOBase:
    with path.open("rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == GZIP_MAGIC else path.open("rb")


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error) or type(error).__name__
