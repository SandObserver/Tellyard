import contextlib
import gzip
import http.client
import io
import ipaddress
import re
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import override

from tellyard.channels import Headers

# Some broadcasters block requests without a browser user agent.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TEXT_LIMIT = 4 * 1024 * 1024
TEXT_DEADLINE = 30.0
DOWNLOAD_LIMIT = 256 * 1024 * 1024
DOWNLOAD_DEADLINE = 600.0
CHUNK = 256 * 1024
GZIP_MAGIC = b"\x1f\x8b"
SCHEMES = frozenset({"http", "https"})
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
# A new context per request reloads every CA certificate. The relay makes a request per video segment.
TLS = ssl.create_default_context()

# Stream URLs carry credentials, and signed ones carry tokens in the path and the query.
URL = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?:[^/\s?#@'\"]*@)?(?P<host>[^/\s?#'\"]+)[^\s'\"]*", re.IGNORECASE)

type Hosts = frozenset[str]
type Connect = Callable[[tuple[str, int], float | None, tuple[str, int] | None], socket.socket]


class FetchError(Exception):
    pass


class BlockedAddressError(OSError):
    pass


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def hosts_of(urls: Iterable[str]) -> Hosts:
    return frozenset(host for url in urls if (host := host_of(url)))


def request(url: str, headers: Headers = (), trusted: Hosts = frozenset()) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    merged.update(dict(headers))
    if host_of(url) not in trusted:
        merged = {key: value for key, value in merged.items() if key.lower() not in CREDENTIAL_HEADERS}
    return urllib.request.Request(url, headers=merged)


@contextlib.contextmanager
def open_url(
    url: str,
    headers: Headers = (),
    trusted: Hosts = frozenset(),
    timeout: float = 10.0,
    deadline: float = TEXT_DEADLINE,
) -> Iterator[http.client.HTTPResponse]:
    # The socket timeout applies to each read. The deadline closes the connection to bound the whole request.
    if urllib.parse.urlsplit(url).scheme.lower() not in SCHEMES:
        raise FetchError(f"{safe_url(url)}: only http and https addresses are allowed")
    watch = _Deadline()
    timer = threading.Timer(deadline, watch.expire)
    timer.start()
    try:
        with _opener(watch, trusted).open(request(url, headers, trusted), timeout=timeout) as response:
            yield response
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        if watch.expired:
            raise FetchError(f"{safe_url(url)}: response took longer than {deadline:.0f} s") from error
        raise FetchError(f"{safe_url(url)}: {_reason(error)}") from error
    finally:
        timer.cancel()
    if watch.expired:
        raise FetchError(f"{safe_url(url)}: response took longer than {deadline:.0f} s")


def fetch_text(
    url: str,
    headers: Headers = (),
    trusted: Hosts = frozenset(),
    timeout: float = 10.0,
    deadline: float = TEXT_DEADLINE,
) -> tuple[str, str]:
    body = bytearray()
    with open_url(url, headers, trusted, timeout, deadline) as response:
        final = response.geturl()
        while len(body) <= TEXT_LIMIT and (chunk := response.read1(CHUNK)):
            body += chunk
    if len(body) > TEXT_LIMIT:
        raise FetchError(f"{safe_url(url)}: response is larger than {TEXT_LIMIT // 1024 // 1024} MB")
    return final, body.decode("utf-8", errors="replace")


def download(
    url: str,
    destination: Path,
    trusted: Hosts = frozenset(),
    timeout: float = 60.0,
    deadline: float = DOWNLOAD_DEADLINE,
) -> None:
    partial = destination.with_name(destination.name + ".part")
    total = 0
    try:
        with open_url(url, (), trusted, timeout, deadline) as response, partial.open("wb") as out:
            while chunk := response.read1(CHUNK):
                total += len(chunk)
                if total > DOWNLOAD_LIMIT:
                    raise FetchError(f"{safe_url(url)}: file is larger than {DOWNLOAD_LIMIT // 1024 // 1024} MB")
                out.write(chunk)
        partial.replace(destination)
    except OSError as error:
        raise FetchError(f"{safe_url(url)}: {_reason(error)}") from error
    finally:
        partial.unlink(missing_ok=True)


def safe_url(url: str) -> str:
    # URLs can carry passwords, tokens and signatures. Logs and error messages show only the host.
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "an invalid address"
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host or "an invalid address"


def redact(text: str) -> str:
    return URL.sub(r"\g<scheme>\g<host>", text)


def is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _opener(watch: "_Deadline", trusted: Hosts) -> urllib.request.OpenerDirector:
    allowed = trusted | hosts_of(urllib.request.getproxies().values())
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        _HttpHandler(watch, allowed),
        _HttpsHandler(watch, allowed),
        _Redirects(allowed),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def _connector(trusted: Hosts) -> Connect:
    def connect(
        address: tuple[str, int], timeout: float | None = None, source: tuple[str, int] | None = None
    ) -> socket.socket:
        host, port = address
        if host.lower() in trusted:
            return socket.create_connection(address, timeout, source)
        # Connect to the checked addresses, not the name, so a second DNS answer cannot change the target.
        addresses = [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        for item in addresses:
            if not is_public(item):
                raise BlockedAddressError(f"refused {item}: private addresses need a host named in the configuration")
        last: OSError | None = None
        for item in addresses:
            try:
                return socket.create_connection((item, port), timeout, source)
            except OSError as error:
                last = error
        raise last or OSError("no address to connect to")

    return connect


class _Deadline:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self.expired = False

    def connection(self, base: type[http.client.HTTPConnection], trusted: Hosts) -> type[http.client.HTTPConnection]:
        watch = self
        connect = _connector(trusted)

        class Watched(base):  # type: ignore[valid-type,misc]
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self._create_connection = connect

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
    def __init__(self, watch: _Deadline, trusted: Hosts) -> None:
        super().__init__()
        self._connection = watch.connection(http.client.HTTPConnection, trusted)

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._connection, req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, watch: _Deadline, trusted: Hosts) -> None:
        super().__init__(context=TLS)
        self._connection = watch.connection(http.client.HTTPSConnection, trusted)

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(self._connection, req)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, trusted: Hosts) -> None:
        self._trusted = trusted

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        scheme = urllib.parse.urlsplit(newurl).scheme.lower()
        if scheme not in SCHEMES:
            raise urllib.error.URLError(f"refused a redirect to a {scheme or 'relative'} address")
        new = super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]
        if new is not None and host_of(newurl) not in self._trusted:
            for name in [name for name in new.headers if name.lower() in CREDENTIAL_HEADERS]:
                del new.headers[name]
        return new


def _shut(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def open_maybe_gzip(path: Path) -> io.BufferedIOBase:
    with path.open("rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == GZIP_MAGIC else path.open("rb")


def _reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error) or type(error).__name__
