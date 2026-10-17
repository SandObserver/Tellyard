import json
import logging
import re
import select
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from tellyard import __version__
from tellyard.channels import Channel
from tellyard.guide import Guide
from tellyard.net import FetchError, fetch_text
from tellyard.resolve import Resolver
from tellyard.settings import Settings
from tellyard.store import ChannelStore
from tellyard.stream import Session, StreamError, ViewerLeftError

log = logging.getLogger(__name__)
STREAM_PATH = re.compile(r"^/(?:stream/|auto/v)(\d{1,5}(?:\.\d{1,3})?)$")
HOST = re.compile(r"^[A-Za-z0-9.\-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:.]+\](:\d{1,5})?$")
GUIDE_CHECK = 60.0
GUIDE_RETRY = timedelta(minutes=15)
HEARTBEAT_SECONDS = 60.0
SOCKET_TIMEOUT = 60.0
WATCH_INTERVAL = 0.5

type SessionFactory = Callable[[Channel], Session]


class App:
    def __init__(
        self,
        settings: Settings,
        store: ChannelStore,
        guide: Guide,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.guide = guide
        resolver = Resolver(settings)
        self._make_session = session_factory or (lambda channel: Session(channel, resolver, settings.ffmpeg))
        self._tuners = threading.BoundedSemaphore(settings.tuners)
        self._lock = threading.Lock()
        self._streams = 0
        self._guide_version = 0
        self._guide_failed_at: datetime | None = None
        self._started_at = datetime.now(UTC)
        self._guide_thread: threading.Thread | None = None
        self._last_heartbeat = float("-inf")

    @property
    def streams(self) -> int:
        with self._lock:
            return self._streams

    def base_url(self, host: str | None) -> str:
        if self.settings.base_url:
            return self.settings.base_url
        if host and HOST.match(host):
            return f"http://{host}"
        return f"http://127.0.0.1:{self.settings.port}"

    def discover(self, base: str) -> dict[str, Any]:
        return {
            "FriendlyName": self.settings.device_name,
            "Manufacturer": "Tellyard",
            # Do not use a TC model. With a TC model, Plex offers tuner transcoding.
            "ModelNumber": "HDHR5-4US",
            "FirmwareName": "hdhomerun5_atsc",
            "FirmwareVersion": __version__,
            "DeviceID": self.settings.device_id,
            "DeviceAuth": "tellyard",
            "BaseURL": base,
            "LineupURL": f"{base}/lineup.json",
            "TunerCount": self.settings.tuners,
        }

    def lineup(self, base: str) -> list[dict[str, Any]]:
        return [
            {
                "GuideNumber": channel.number,
                "GuideName": channel.name,
                "URL": f"{base}/stream/{channel.number}",
                "HD": 1,
            }
            for channel in self.store.current().channels
        ]

    def playlist(self, base: str) -> str:
        lines = [f'#EXTM3U url-tvg="{base}/guide.xml"']
        for channel in self.store.current().channels:
            name = channel.name.replace(",", " ")
            lines.append(
                f'#EXTINF:-1 tvg-id="{channel.guide_id}" tvg-chno="{channel.number}" tvg-logo="{channel.logo}",{name}'
            )
            lines.append(f"{base}/stream/{channel.number}")
        return "\n".join(lines) + "\n"

    def device_xml(self, base: str) -> str:
        name = escape(self.settings.device_name)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<root xmlns="urn:schemas-upnp-org:device-1-0">'
            "<specVersion><major>1</major><minor>0</minor></specVersion>"
            f"<URLBase>{escape(base)}</URLBase>"
            "<device><deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>"
            f"<friendlyName>{name}</friendlyName><manufacturer>Tellyard</manufacturer>"
            f"<modelName>Tellyard</modelName><modelNumber>{escape(__version__)}</modelNumber>"
            f"<serialNumber>{self.settings.device_id}</serialNumber>"
            f"<UDN>uuid:{self.settings.device_id}</UDN></device></root>"
        )

    def problem(self, now: datetime | None = None) -> str | None:
        if self._guide_thread is not None and not self._guide_thread.is_alive():
            return "the guide thread has stopped"
        if not self.store.current().channels:
            return f"no channels yet: add them to {self.settings.channels_file}"
        age = (now or datetime.now(UTC)) - (self.guide.built_at or self._started_at)
        limit = timedelta(hours=2 * self.settings.guide_refresh_hours)
        if age > limit:
            return f"the guide is {age.total_seconds() / 3600:.0f} hours old"
        return None

    def health(self) -> dict[str, Any]:
        built = self.guide.built_at
        problem = self.problem()
        return {
            "status": "unhealthy" if problem else "ok",
            "reason": problem,
            "version": __version__,
            "channels": len(self.store.current().channels),
            "streams": self.streams,
            "guide_built_at": built.isoformat() if built else None,
        }

    def acquire_tuner(self) -> bool:
        if not self._tuners.acquire(blocking=False):
            return False
        with self._lock:
            self._streams += 1
        return True

    def release_tuner(self) -> None:
        with self._lock:
            self._streams -= 1
        self._tuners.release()

    def session(self, channel: Channel) -> Session:
        return self._make_session(channel)

    def refresh_guide(self, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        lineup = self.store.current()
        if not self._guide_due(moment):
            return
        try:
            self.guide.rebuild(lineup, moment)
        except Exception:
            log.exception("guide rebuild failed", extra={"retry_min": GUIDE_RETRY.seconds // 60})
            self._guide_failed_at = moment
        else:
            self._guide_failed_at = None
        self._guide_version = self.store.version

    def start(self, stop: threading.Event) -> None:
        self._guide_thread = threading.Thread(target=self.guide_loop, args=(stop,), name="guide", daemon=True)
        self._guide_thread.start()

    def guide_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.refresh_guide()
                self.heartbeat()
            except Exception:
                log.exception("guide check failed", extra={"retry_s": round(GUIDE_CHECK)})
            stop.wait(GUIDE_CHECK)

    def heartbeat(self) -> None:
        now = time.monotonic()
        url = self.settings.heartbeat_url
        if not url or now - self._last_heartbeat < HEARTBEAT_SECONDS or self.problem():
            return
        self._last_heartbeat = now
        try:
            fetch_text(url)
        except FetchError as error:
            log.warning("HEARTBEAT_URL call failed", extra={"reason": str(error)})

    def _guide_due(self, now: datetime) -> bool:
        if self._guide_failed_at and now - self._guide_failed_at < GUIDE_RETRY:
            return False
        built = self.guide.built_at
        refresh = timedelta(hours=self.settings.guide_refresh_hours)
        return self._guide_version != self.store.version or built is None or now - built >= refresh


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: App) -> None:
        super().__init__(address, Handler)
        self.app = app


class Handler(BaseHTTPRequestHandler):
    server: Server
    server_version = "Tellyard"
    timeout = SOCKET_TIMEOUT

    def do_GET(self) -> None:
        app = self.server.app
        path = urlsplit(self.path).path
        base = app.base_url(self.headers.get("Host"))
        if stream := STREAM_PATH.match(path):
            self._stream(stream.group(1))
        elif path == "/discover.json":
            self._json(app.discover(base))
        elif path == "/lineup.json":
            self._json(app.lineup(base))
        elif path == "/lineup_status.json":
            self._json({"ScanInProgress": 0, "ScanPossible": 1, "Source": "Cable", "SourceList": ["Cable"]})
        elif path in {"/guide.xml", "/epg.xml"}:
            self._guide()
        elif path == "/playlist.m3u":
            self._send(HTTPStatus.OK, "audio/x-mpegurl; charset=utf-8", app.playlist(base).encode())
        elif path == "/device.xml":
            self._send(HTTPStatus.OK, "application/xml; charset=utf-8", app.device_xml(base).encode())
        elif path in {"/healthz", "/health"}:
            health = app.health()
            status = HTTPStatus.OK if health["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
            self._send(status, "application/json", json.dumps(health).encode())
        else:
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")

    def do_POST(self) -> None:
        if urlsplit(self.path).path == "/lineup.post":
            self._send(HTTPStatus.OK, "text/plain; charset=utf-8", b"")
        else:
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("request", extra={"client": self.address_string(), "line": format % args})

    def _guide(self) -> None:
        data = self.server.app.guide.data
        if data is None:
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8", b"the guide is being built\n")
        else:
            self._send(HTTPStatus.OK, "application/xml; charset=utf-8", data)

    def _stream(self, number: str) -> None:
        app = self.server.app
        channel = app.store.current().find(number)
        if channel is None:
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"no such channel\n")
            return
        if not app.acquire_tuner():
            log.warning(
                "all tuners are busy",
                extra={"channel": channel.number, "title": channel.name, "tuners": app.settings.tuners},
            )
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8", b"all tuners are busy\n")
            return
        try:
            with app.session(channel) as session:
                done = threading.Event()
                watcher = threading.Thread(
                    target=self._watch, args=(session.cancel, done), name=f"watch {channel.number}", daemon=True
                )
                watcher.start()
                try:
                    self._play(session)
                finally:
                    done.set()
                    watcher.join()
        finally:
            app.release_tuner()

    def _watch(self, cancel: threading.Event, done: threading.Event) -> None:
        # Clients send nothing after the request, so a readable socket with no data means the viewer left.
        poller = select.poll()
        poller.register(self.connection, select.POLLIN)
        while not done.is_set():
            try:
                readable = poller.poll(WATCH_INTERVAL * 1000)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    break
            except OSError:
                break
            if readable:
                done.wait(WATCH_INTERVAL)
        if not done.is_set():
            cancel.set()

    def _play(self, session: Session) -> None:
        began = time.monotonic()
        try:
            session.open()
        except ViewerLeftError:
            log.info("viewer left before the stream started", extra=session.fields)
            return
        except StreamError as error:
            log.warning("stream failed to start", extra={**session.fields, "reason": str(error)})
            self._send(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8", f"{error}\n".encode())
            return
        log.info("stream started", extra={**session.fields, "client": self.client_address[0], "height": session.height})
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for chunk in session.chunks():
                self.wfile.write(chunk)
        except OSError:
            pass
        log.info("stream stopped", extra={**session.fields, "seconds": round(time.monotonic() - began)})

    def _json(self, value: object) -> None:
        self._send(HTTPStatus.OK, "application/json", json.dumps(value).encode())

    def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
