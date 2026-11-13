import contextlib
import ipaddress
import logging
import re
import select
import socket
import socketserver
import threading
from collections.abc import Iterator

from tellyard import net
from tellyard.net import BlockedAddressError, Trust

log = logging.getLogger(__name__)
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 60.0
CHUNK = 64 * 1024
HOSTNAME = re.compile(r"^[A-Za-z0-9_.-]{1,253}$")
VERSION = 5
NO_AUTHENTICATION = 0
NO_METHOD = 0xFF
CONNECT = 1
IPV4, DOMAIN, IPV6 = 1, 3, 4
SUCCEEDED, FAILED, NOT_ALLOWED, NOT_SUPPORTED, BAD_ADDRESS = 0, 1, 2, 7, 8


@contextlib.contextmanager
def serve(trust: Trust, label: str) -> Iterator[str]:
    # yt-dlp reaches the network only through this SOCKS5 proxy, which applies the same address checks as Tellyard.
    server = _Server(trust, label)
    threading.Thread(target=server.serve_forever, name=f"egress {label}", daemon=True).start()
    try:
        yield f"socks5h://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        server.close_tunnels()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, trust: Trust, label: str) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.trust = trust
        self.label = label
        self._lock = threading.Lock()
        self._open: set[socket.socket] = set()
        self._closed = False

    def track(self, sock: socket.socket) -> bool:
        with self._lock:
            if not self._closed:
                self._open.add(sock)
            return not self._closed

    def untrack(self, sock: socket.socket) -> None:
        with self._lock:
            self._open.discard(sock)

    def close_tunnels(self) -> None:
        with self._lock:
            self._closed = True
            for sock in self._open:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)


class _Handler(socketserver.BaseRequestHandler):
    server: _Server

    def handle(self) -> None:
        client: socket.socket = self.request
        client.settimeout(IDLE_TIMEOUT)
        if not self.server.track(client):
            return
        try:
            target = _negotiate(client)
            if target is not None:
                self._connect(client, *target)
        except OSError:
            pass
        finally:
            self.server.untrack(client)

    def _connect(self, client: socket.socket, host: str, port: int) -> None:
        try:
            upstream = net.connect_out(host, port, self.server.trust, CONNECT_TIMEOUT)
        except BlockedAddressError as error:
            log.warning("source request failed", extra={"channel": self.server.label, "reason": str(error)})
            _reply(client, NOT_ALLOWED)
            return
        except (OSError, ValueError):
            _reply(client, FAILED)
            return
        with upstream:
            if not self.server.track(upstream):
                return
            try:
                upstream.settimeout(IDLE_TIMEOUT)
                _reply(client, SUCCEEDED)
                _pipe(client, upstream)
            finally:
                self.server.untrack(upstream)


def _negotiate(client: socket.socket) -> tuple[str, int] | None:
    version, count = _read(client, 2)
    methods = _read(client, count)
    if version != VERSION or NO_AUTHENTICATION not in methods:
        client.sendall(bytes([VERSION, NO_METHOD]))
        return None
    client.sendall(bytes([VERSION, NO_AUTHENTICATION]))
    version, command, _, kind = _read(client, 4)
    host = ""
    if kind == IPV4:
        host = str(ipaddress.IPv4Address(_read(client, 4)))
    elif kind == IPV6:
        host = str(ipaddress.IPv6Address(_read(client, 16)))
    elif kind == DOMAIN:
        name = _read(client, _read(client, 1)[0]).decode("ascii", errors="replace")
        host = name if HOSTNAME.match(name) else ""
    else:
        _reply(client, BAD_ADDRESS)
        return None
    port = int.from_bytes(_read(client, 2))
    if version != VERSION or command != CONNECT:
        _reply(client, NOT_SUPPORTED)
        return None
    if not host or not port:
        _reply(client, BAD_ADDRESS)
        return None
    return host, port


def _read(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise OSError("the client closed the connection")
        data += block
    return bytes(data)


def _reply(client: socket.socket, code: int) -> None:
    with contextlib.suppress(OSError):
        client.sendall(bytes([VERSION, code, 0, IPV4, 0, 0, 0, 0, 0, 0]))


def _pipe(client: socket.socket, upstream: socket.socket) -> None:
    peers = {client: upstream, upstream: client}
    while True:
        ready, _, _ = select.select(list(peers), [], [], IDLE_TIMEOUT)
        if not ready:
            return
        for sock in ready:
            data = sock.recv(CHUNK)
            if not data:
                return
            peers[sock].sendall(data)
