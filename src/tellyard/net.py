import base64
import contextlib
import gzip
import http.client
import io
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, override

from tellyard.channels import Headers

# Some broadcasters block requests without a browser user agent.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TEXT_LIMIT = 4 * 1024 * 1024
TEXT_DEADLINE = 30.0
READ_TIMEOUT = 10.0
DOWNLOAD_LIMIT = 256 * 1024 * 1024
DOWNLOAD_DEADLINE = 600.0
DOWNLOAD_READ_TIMEOUT = 60.0
CHUNK = 256 * 1024
GZIP_MAGIC = b"\x1f\x8b"
SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}
CREDENTIAL_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})
# A new context per request reloads every CA certificate. The relay makes a request per video segment.
TLS = ssl.create_default_context()
TLS.set_alpn_protocols(["http/1.1"])
TLS.post_handshake_auth = True

# Stream URLs carry credentials, and signed ones carry tokens in the path and the query.
URL = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?:[^/\s?#@'\"]*@)?(?P<host>[^/\s?#'\"]+)[^\s'\"]*", re.IGNORECASE)

NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")
# Azure answers metadata requests on this public address.
METADATA = frozenset({ipaddress.IPv4Address("168.63.129.16")})
INTERFACES = Path("/proc/net/if_inet6")
INTERFACE_CHECK = 60.0
PROXY_HEAD_LIMIT = 16 * 1024
REFUSED = "private addresses need a host and port named in the configuration"

type Origin = tuple[str, str, int]
type IP = ipaddress.IPv4Address | ipaddress.IPv6Address
type Connect = Callable[[tuple[str, int], float | None, tuple[str, int] | None], socket.socket]


class FetchError(Exception):
    pass


class Limits(NamedTuple):
    read: float = READ_TIMEOUT
    total: float = TEXT_DEADLINE


TEXT = Limits()


class TooLargeError(FetchError):
    pass


class BlockedAddressError(OSError):
    pass


class Cancel(threading.Event):
    def __init__(self) -> None:
        super().__init__()
        self._hooks_lock = threading.Lock()
        self._hooks: list[Callable[[], None]] = []

    @override
    def set(self) -> None:
        super().set()
        with self._hooks_lock:
            hooks, self._hooks = self._hooks, []
        for hook in hooks:
            hook()

    @contextlib.contextmanager
    def on_set(self, hook: Callable[[], None]) -> Iterator[None]:
        with self._hooks_lock:
            waiting = not self.is_set()
            if waiting:
                self._hooks.append(hook)
        if not waiting:
            hook()
        try:
            yield
        finally:
            with self._hooks_lock, contextlib.suppress(ValueError):
                self._hooks.remove(hook)


@dataclass(frozen=True)
class Trust:
    origins: frozenset[Origin] = frozenset()
    ports: frozenset[tuple[str, int]] = frozenset()

    def reaches(self, host: str, port: int) -> bool:
        try:
            return (canonical_host(host), port) in self.ports
        except ValueError:
            return False

    def sends_credentials(self, url: str) -> bool:
        origin = origin_of(url)
        if origin is None:
            return False
        scheme, host, port = origin
        return origin in self.origins or ((scheme, port) == ("https", 443) and ("http", host, 80) in self.origins)


NOBODY = Trust()


def trust_of(urls: Iterable[str], extra: Iterable[tuple[str, int]] = ()) -> Trust:
    origins = frozenset(origin for url in urls if (origin := origin_of(url)))
    ports = {(host, port) for _, host, port in origins}
    ports |= {(host, 443) for scheme, host, port in origins if (scheme, port) == ("http", 80)}
    for host, port in extra:
        with contextlib.suppress(ValueError):
            ports.add((canonical_host(host), port))
    return Trust(origins, frozenset(ports))


def origin_of(url: str) -> Origin | None:
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        port = parts.port or DEFAULT_PORTS.get(scheme)
        host = canonical_host(parts.hostname or "")
    except ValueError:
        return None
    if scheme not in SCHEMES or not host or port is None:
        return None
    return scheme, host, port


def canonical_host(host: str) -> str:
    host = host.lower()
    with contextlib.suppress(ValueError):
        return str(ipaddress.ip_address(host))
    if (number := ipv4_number(host)) is not None:
        return str(number)
    return host.removesuffix(".")


def ipv4_number(host: str) -> ipaddress.IPv4Address | None:
    # The WHATWG URL rules: a host whose last label is a number is an IPv4 address, such as 127.1 or 0x7f000001.
    parts = host.split(".")
    if len(parts) > 1 and not parts[-1]:
        parts.pop()
    if not (parts[-1].isascii() and parts[-1].isdigit()) and _ipv4_part(parts[-1]) is None:
        return None
    numbers = [_ipv4_part(part) for part in parts]
    if len(numbers) > 4 or any(number is None for number in numbers):
        raise ValueError(f"{host} is not a valid IPv4 address")
    values = [number for number in numbers if number is not None]
    if any(value > 255 for value in values[:-1]) or values[-1] >= 256 ** (5 - len(values)):
        raise ValueError(f"{host} is not a valid IPv4 address")
    return ipaddress.IPv4Address(values[-1] + sum(value << 8 * (3 - index) for index, value in enumerate(values[:-1])))


def _ipv4_part(part: str) -> int | None:
    base, digits = 10, part
    if part[:2].lower() == "0x":
        base, digits = 16, part[2:]
    elif len(part) > 1 and part[0] == "0":
        base, digits = 8, part[1:]
    allowed = {10: "0123456789", 8: "01234567", 16: "0123456789abcdefABCDEF"}[base]
    if not part or any(character not in allowed for character in digits):
        return None
    return int(digits, base) if digits else 0


def request(url: str, headers: Headers = (), trust: Trust = NOBODY) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    merged.update(dict(headers))
    if not trust.sends_credentials(url):
        merged = {key: value for key, value in merged.items() if key.lower() not in CREDENTIAL_HEADERS}
    return urllib.request.Request(_encoded(url), headers=merged)


def _encoded(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~")
    query = urllib.parse.quote(parts.query, safe="/%:@!$&'()*+,;=~?")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


@contextlib.contextmanager
def open_url(
    url: str,
    headers: Headers = (),
    trust: Trust = NOBODY,
    *,
    limits: Limits = TEXT,
    cancel: Cancel | None = None,
) -> Iterator[http.client.HTTPResponse]:
    # The read limit applies to each read. The total limit closes the connection to bound the whole request.
    deadline = limits.total
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError:
        raise FetchError(f"{safe_url(url)}: the address has characters that are not allowed") from None
    if scheme not in SCHEMES:
        raise FetchError(f"{safe_url(url)}: only http and https addresses are allowed")
    watch = _Deadline()
    timer = threading.Timer(deadline, watch.expire)
    timer.start()
    stop = cancel.on_set(watch.abort) if cancel is not None else contextlib.nullcontext()
    try:
        with stop, _opener(watch, trust).open(request(url, headers, trust), timeout=limits.read) as response:
            yield response
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as error:
        raise FetchError(f"{safe_url(url)}: {watch.reason(deadline) or _reason(error)}") from error
    finally:
        timer.cancel()
    # A shut connection reads as a short body.
    if reason := watch.reason(deadline):
        raise FetchError(f"{safe_url(url)}: {reason}")


def fetch_text(
    url: str,
    headers: Headers = (),
    trust: Trust = NOBODY,
    *,
    deadline: float = TEXT_DEADLINE,
    cancel: Cancel | None = None,
) -> tuple[str, str]:
    body = bytearray()
    with open_url(url, headers, trust, limits=Limits(READ_TIMEOUT, deadline), cancel=cancel) as response:
        final = response.geturl()
        while len(body) <= TEXT_LIMIT and (block := read_block(response, TEXT_LIMIT + 1 - len(body))):
            body += block
    if len(body) > TEXT_LIMIT:
        raise TooLargeError(f"{safe_url(url)}: response is larger than {TEXT_LIMIT // 1024 // 1024} MB")
    return final, body.decode("utf-8", errors="replace")


def read_block(response: http.client.HTTPResponse, size: int) -> bytes:
    # read() keeps each piece of a chunked body as its own object. Tiny pieces then use about 90 times their size.
    return response.read1(size) if response.chunked else response.read(size)


def download(
    url: str,
    destination: Path,
    trust: Trust = NOBODY,
    *,
    deadline: float = DOWNLOAD_DEADLINE,
) -> None:
    partial = destination.with_name(destination.name + ".part")
    total = 0
    try:
        limits = Limits(DOWNLOAD_READ_TIMEOUT, deadline)
        with open_url(url, (), trust, limits=limits) as response, partial.open("wb") as out:
            while chunk := response.read1(CHUNK):
                total += len(chunk)
                if total > DOWNLOAD_LIMIT:
                    raise TooLargeError(f"{safe_url(url)}: file is larger than {DOWNLOAD_LIMIT // 1024 // 1024} MB")
                out.write(chunk)
        partial.replace(destination)
    except OSError as error:
        raise FetchError(f"{safe_url(url)}: {_reason(error)}") from error
    finally:
        partial.unlink(missing_ok=True)


def safe_url(url: str) -> str:
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
        ip: IP = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in NAT64 or ip in IPV4_COMPATIBLE:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif any(ip in network for network in LOCAL.networks()):
            return False
    return ip.is_global and not ip.is_multicast and ip not in METADATA


class _Interfaces:
    # Global IPv6 addresses are public by type. The networks of this host's own interfaces are the home network.
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._checked = -INTERFACE_CHECK
        self._networks: tuple[ipaddress.IPv6Network, ...] = ()

    def networks(self) -> tuple[ipaddress.IPv6Network, ...]:
        with self._lock:
            now = time.monotonic()
            if now - self._checked >= INTERFACE_CHECK:
                self._checked, self._networks = now, self._read()
            return self._networks

    def _read(self) -> tuple[ipaddress.IPv6Network, ...]:
        try:
            lines = self._path.read_text(encoding="ascii").splitlines()
        except (OSError, UnicodeDecodeError):
            return ()
        networks: list[ipaddress.IPv6Network] = []
        for line in lines:
            fields = line.split()
            if len(fields) < 6 or fields[3] != "00":
                continue
            with contextlib.suppress(ValueError):
                address = ipaddress.IPv6Address(int(fields[0], 16))
                networks.append(ipaddress.IPv6Network((address, int(fields[2], 16)), strict=False))
        return tuple(networks)


LOCAL = _Interfaces(INTERFACES)


def without_proxies(env: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in env.items() if not key.lower().endswith("_proxy")}


def connect_out(host: str, port: int, trust: Trust, timeout: float) -> socket.socket:
    refuse_private_number(host, port, trust)
    proxy = _proxy_for(host, port)
    if proxy is None:
        return _connector(trust, proxy=False)((host, port), timeout, None)
    return _tunnel(proxy, host, port, timeout)


def refuse_private_number(host: str, port: int, trust: Trust) -> None:
    # Behind a proxy the connection goes to the proxy, so the connector never sees this address.
    if trust.reaches(host, port):
        return
    try:
        address = canonical_host(host)
    except ValueError:
        raise BlockedAddressError(f"refused {host}: not a valid address") from None
    try:
        ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return
    if not is_public(address):
        raise BlockedAddressError(f"refused {_host_port(address, port)}: {REFUSED}")


def _host_port(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _opener(watch: "_Deadline", trust: Trust) -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        _HttpHandler(watch, trust),
        _HttpsHandler(watch, trust),
        _Redirects(trust),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def _check_target(url: str, trust: Trust) -> None:
    parts = urllib.parse.urlsplit(url)
    refuse_private_number(parts.hostname or "", parts.port or DEFAULT_PORTS.get(parts.scheme.lower(), 0), trust)


def _proxied(req: urllib.request.Request) -> bool:
    # ProxyHandler points the connection at the proxy. The tunnel or the full URL then holds the real target.
    return req.has_proxy() or getattr(req, "_tunnel_host", None) is not None


def _connector(trust: Trust, proxy: bool) -> Connect:
    def connect(
        address: tuple[str, int], timeout: float | None = None, source: tuple[str, int] | None = None
    ) -> socket.socket:
        host, port = address
        if proxy or trust.reaches(host, port):
            return socket.create_connection(address, timeout, source)
        # Connect to the checked addresses, not the name, so a second DNS answer cannot change the target.
        addresses = [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
        for item in addresses:
            if not is_public(item):
                raise BlockedAddressError(f"refused {_host_port(item, port)}: {REFUSED}")
        last: OSError | None = None
        for item in addresses:
            try:
                return socket.create_connection((item, port), timeout, source)
            except OSError as error:
                last = error
        raise last or OSError("no address to connect to")

    return connect


def _proxy_for(host: str, port: int) -> urllib.parse.SplitResult | None:
    proxies = urllib.request.getproxies()
    order = ("http", "https") if port == DEFAULT_PORTS["http"] else ("https", "http")
    value = next((proxies[name] for name in order if proxies.get(name)), None)
    if value is None or urllib.request.proxy_bypass(host):
        return None
    return urllib.parse.urlsplit(value if "://" in value else f"http://{value}")


def _tunnel(proxy: urllib.parse.SplitResult, host: str, port: int, timeout: float) -> socket.socket:
    if proxy.scheme.lower() != "http" or not proxy.hostname:
        raise OSError("only http proxies are supported")
    target = _host_port(host, port)
    lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
    if proxy.username is not None:
        user = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}"
        lines.append(f"Proxy-Authorization: Basic {base64.b64encode(user.encode()).decode()}")
    sock = socket.create_connection((proxy.hostname, proxy.port or DEFAULT_PORTS["http"]), timeout)
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        status = _read_head(sock).split(b"\r\n", 1)[0].split(b" ")
        if len(status) < 2 or status[1] != b"200":
            raise OSError(f"the proxy refused {target}")
    except BaseException:
        sock.close()
        raise
    return sock


def _read_head(sock: socket.socket) -> bytes:
    # Read byte by byte: any data after the head belongs to the tunnel.
    head = bytearray()
    while not head.endswith(b"\r\n\r\n"):
        if len(head) > PROXY_HEAD_LIMIT:
            raise OSError("the proxy answer is too long")
        byte = sock.recv(1)
        if not byte:
            raise OSError("the proxy closed the connection")
        head += byte
    return bytes(head)


class _Deadline:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: list[socket.socket] = []
        self.expired = False
        self.aborted = False

    def connection(
        self, base: type[http.client.HTTPConnection], trust: Trust, proxy: bool
    ) -> type[http.client.HTTPConnection]:
        watch = self
        connect = _connector(trust, proxy)

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
            if self.expired or self.aborted:
                _shut(sock)

    def expire(self) -> None:
        with self._lock:
            self.expired = True
            self._shut_all()

    def abort(self) -> None:
        with self._lock:
            self.aborted = True
            self._shut_all()

    def reason(self, deadline: float) -> str | None:
        if self.aborted:
            return "cancelled"
        if self.expired:
            return f"response took longer than {deadline:.0f} s"
        return None

    def _shut_all(self) -> None:
        for sock in self._sockets:
            _shut(sock)


class _HttpHandler(urllib.request.HTTPHandler):
    def __init__(self, watch: _Deadline, trust: Trust) -> None:
        super().__init__()
        self._trust = trust
        self._direct = watch.connection(http.client.HTTPConnection, trust, proxy=False)
        self._proxied = watch.connection(http.client.HTTPConnection, trust, proxy=True)

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        _check_target(req.full_url, self._trust)
        return self.do_open(self._proxied if _proxied(req) else self._direct, req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, watch: _Deadline, trust: Trust) -> None:
        super().__init__(context=TLS)
        self._trust = trust
        self._direct = watch.connection(http.client.HTTPSConnection, trust, proxy=False)
        self._proxied = watch.connection(http.client.HTTPSConnection, trust, proxy=True)

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        _check_target(req.full_url, self._trust)
        return self.do_open(self._proxied if _proxied(req) else self._direct, req, context=TLS)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, trust: Trust) -> None:
        self._trust = trust

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
        if new is not None and not self._trust.sends_credentials(newurl):
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
    if isinstance(error, (http.client.InvalidURL, ValueError)):
        return "the address has characters that are not allowed"
    return str(error) or type(error).__name__
