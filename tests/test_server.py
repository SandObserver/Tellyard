import json
import os
import resource
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tellyard import server
from tellyard.channels import Channel
from tellyard.guide import Guide
from tellyard.relay import Relay
from tellyard.resolve import LookupCancelledError, Resolved
from tellyard.server import App, Server
from tellyard.settings import Settings
from tellyard.store import ChannelStore
from tellyard.stream import Session
from tests.fakes import FakeResolver, fake_ffmpeg


@pytest.fixture
def app(relay: Relay, make_settings: Callable[..., Settings], channels_file: Path, tmp_path: Path) -> App:
    settings = make_settings(TUNERS="1")
    resolver = FakeResolver(settings)
    ffmpeg = str(fake_ffmpeg(tmp_path))
    guide = Guide(tmp_path / "data" / "guide.xml", 1, downloader=lambda url, path: None)

    def session(channel: Channel) -> Session:
        return Session(channel, resolver, ffmpeg, relay)

    return App(settings, ChannelStore(channels_file), guide, session_factory=session)


@pytest.fixture
def base(app: App) -> Iterator[str]:
    server = Server(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(url: str, method: str = "GET") -> tuple[int, str, bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def test_discover_uses_the_request_host(base: str, app: App) -> None:
    status, _, body = get(f"{base}/discover.json")
    data = json.loads(body)
    assert status == 200
    assert data["BaseURL"] == base
    assert data["LineupURL"] == f"{base}/lineup.json"
    assert data["TunerCount"] == 1
    assert data["DeviceID"] == app.settings.device_id
    assert "TC" not in data["ModelNumber"]


def test_lineup_lists_every_channel(base: str) -> None:
    lineup = json.loads(get(f"{base}/lineup.json")[2])
    assert [item["GuideNumber"] for item in lineup] == ["101", "201"]
    assert lineup[0]["URL"] == f"{base}/stream/101"


def test_status_post_device_and_playlist(base: str) -> None:
    assert json.loads(get(f"{base}/lineup_status.json")[2])["ScanPossible"] == 1
    assert get(f"{base}/lineup.post?scan=start", method="POST")[0] == 200
    assert get(f"{base}/other", method="POST")[0] == 404
    assert b"<friendlyName>Tellyard</friendlyName>" in get(f"{base}/device.xml")[2]
    playlist = get(f"{base}/playlist.m3u")[2].decode()
    assert f'url-tvg="{base}/guide.xml"' in playlist
    assert 'tvg-id="tellyard.101"' in playlist
    assert 'tvg-logo="https://example.com/news.png"' in playlist
    assert get(f"{base}/missing")[0] == 404


def test_guide_is_served_once_built(base: str, app: App) -> None:
    assert get(f"{base}/guide.xml")[0] == 503
    app.refresh_guide()
    status, kind, body = get(f"{base}/epg.xml")
    assert status == 200
    assert kind.startswith("application/xml")
    assert b'<channel id="tellyard.201">' in body
    assert b'<icon src="https://example.com/drama.png" />' in body
    assert get(f"{base}/logos/201.png")[0] == 404


def test_guide_entries_get_episode_numbers(base: str, app: App) -> None:
    app.refresh_guide()
    body = get(f"{base}/guide.xml")[2].decode()
    assert body.count("<icon ") == 2
    assert '<episode-num system="original-air-date">' in body
    assert "<category>News</category>" in body


def test_health(base: str) -> None:
    status, _, body = get(f"{base}/healthz")
    data = json.loads(body)
    assert status == 200
    assert data["status"] == "ok"
    assert data["reason"] is None
    assert data["channels"] == 2
    assert data["streams"] == 0
    assert json.loads(get(f"{base}/health")[2])["status"] == "ok"


def test_health_fails_when_the_guide_is_old(base: str, app: App) -> None:
    app.refresh_guide(datetime.now(UTC) - timedelta(hours=13))
    status, _, body = get(f"{base}/healthz")
    assert status == 503
    data = json.loads(body)
    assert (data["status"], data["reason"]) == ("unhealthy", "the guide is 13 hours old")
    app.refresh_guide(datetime.now(UTC))
    assert app.problem() is None


def test_health_fails_when_the_guide_thread_stops(app: App) -> None:
    stop = threading.Event()
    stop.set()
    app.start(stop)
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    while app.problem() is None and datetime.now(UTC) < deadline:
        threading.Event().wait(0.01)
    assert app.problem() == "the guide thread has stopped"


def test_health_fails_without_channels(make_settings: Callable[..., Settings], tmp_path: Path) -> None:
    missing = tmp_path / "config" / "channels.toml"
    settings = make_settings(CHANNELS_FILE=str(missing))
    guide = Guide(tmp_path / "data" / "guide.xml", 1, downloader=lambda url, path: None)
    app = App(settings, ChannelStore(missing), guide)
    app.refresh_guide()
    assert app.lineup("http://tellyard") == []
    assert app.problem() == f"no channels yet: add them to {missing}"


@pytest.fixture
def pings() -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Ping(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.path)
            self.send_response(200 if self.path == "/ok" else 500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Ping)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", seen
    server.shutdown()
    server.server_close()


def make_app(settings: Settings, channels_file: Path, tmp_path: Path) -> App:
    guide = Guide(tmp_path / "data" / "guide.xml", 1, downloader=lambda url, path: None)
    return App(settings, ChannelStore(channels_file), guide)


def test_heartbeat_is_called_once_a_minute_while_healthy(
    make_settings: Callable[..., Settings],
    channels_file: Path,
    tmp_path: Path,
    pings: tuple[str, list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    url, seen = pings
    app = make_app(make_settings(HEARTBEAT_URL=f"{url}/ok"), channels_file, tmp_path)
    app.heartbeat()
    app.heartbeat()
    assert seen == ["/ok"]
    failing = make_app(make_settings(HEARTBEAT_URL=f"{url}/fail"), channels_file, tmp_path)
    failing.heartbeat()
    assert "HEARTBEAT_URL call failed" in caplog.text
    stale = make_app(make_settings(HEARTBEAT_URL=f"{url}/ok"), channels_file, tmp_path)
    stale.refresh_guide(datetime.now(UTC) - timedelta(hours=13))
    stale.heartbeat()
    assert seen == ["/ok", "/fail"]


def test_streams_a_channel_and_frees_the_tuner(base: str, app: App) -> None:
    with urllib.request.urlopen(f"{base}/auto/v101", timeout=10) as response:
        assert response.headers["Content-Type"] == "video/mp2t"
        assert response.read(188 * 20)
        assert app.streams == 1
        assert get(f"{base}/stream/201")[0] == 503
    for _ in range(100):
        if app.streams == 0:
            break
        threading.Event().wait(0.05)
    assert app.streams == 0


def test_unknown_channel(base: str) -> None:
    assert get(f"{base}/stream/999")[0] == 404


def test_channel_that_cannot_start(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "fail")
    status, _, body = get(f"{base}/stream/101")
    assert status == 503
    assert b"no video arrived" in body


def test_base_url_setting_wins(make_settings: Callable[..., Settings], channels_file: Path, tmp_path: Path) -> None:
    settings = make_settings(BASE_URL="http://10.0.0.5:5004")
    app = App(settings, ChannelStore(channels_file), Guide(tmp_path / "g.xml", 1))
    assert app.base_url("evil.example.com") == "http://10.0.0.5:5004"


def test_bad_host_header_falls_back(app: App) -> None:
    assert app.base_url("bad host/<x>") == "http://127.0.0.1:5004"
    assert app.base_url(None) == "http://127.0.0.1:5004"
    assert app.base_url("[::1]:5004") == "http://[::1]:5004"


def test_guide_refresh_schedule(app: App, channels_file: Path) -> None:
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    app.refresh_guide(now)
    first = app.guide.built_at
    app.refresh_guide(now + timedelta(hours=1))
    assert app.guide.built_at == first
    app.refresh_guide(now + timedelta(hours=7))
    assert app.guide.built_at == now + timedelta(hours=7)
    channels_file.write_text(channels_file.read_text(encoding="utf-8").replace("Drama", "Comedy"), encoding="utf-8")
    app.refresh_guide(now + timedelta(hours=7, minutes=1))
    assert app.guide.built_at == now + timedelta(hours=7, minutes=1)


def test_failed_guide_source_is_retried_soon(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[datetime, bool]] = []

    def partly_failed(lineup: object, now: datetime, retry: bool = False) -> None:
        calls.append((now, retry))
        app.guide.built_at = now
        app.guide.failed_sources = 1 if len(calls) == 1 else 0

    monkeypatch.setattr(app.guide, "rebuild", partly_failed)
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    app.refresh_guide(now)
    app.refresh_guide(now + timedelta(minutes=10))
    app.refresh_guide(now + timedelta(minutes=15))
    app.refresh_guide(now + timedelta(minutes=30))
    assert calls == [(now, False), (now + timedelta(minutes=15), True)]


def test_retries_do_not_delay_the_full_refresh(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[datetime, bool]] = []

    def always_failing(lineup: object, now: datetime, retry: bool = False) -> None:
        calls.append((now, retry))
        app.guide.built_at = now
        app.guide.failed_sources = 1

    monkeypatch.setattr(app.guide, "rebuild", always_failing)
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    for minutes in range(0, 7 * 60 + 1, 15):
        app.refresh_guide(now + timedelta(minutes=minutes))
    full = [moment for moment, retry in calls if not retry]
    assert full == [now, now + timedelta(hours=6)]
    assert len(calls) == 7 * 4 + 1


def test_failed_guide_rebuild_waits_before_retrying(
    app: App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[datetime] = []

    def broken(lineup: object, now: datetime, retry: bool = False) -> None:
        calls.append(now)
        raise RuntimeError("disk full")

    monkeypatch.setattr(app.guide, "rebuild", broken)
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    app.refresh_guide(now)
    app.refresh_guide(now + timedelta(minutes=5))
    app.refresh_guide(now + timedelta(minutes=16))
    assert calls == [now, now + timedelta(minutes=16)]
    assert "guide rebuild failed" in caplog.text


def test_guide_loop_stops(app: App) -> None:
    stop = threading.Event()
    stop.set()
    app.guide_loop(stop)


def test_guide_loop_survives_an_unexpected_error(
    app: App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop = threading.Event()
    calls: list[int] = []

    def refresh() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid continuation byte")
        stop.set()

    monkeypatch.setattr(server, "GUIDE_CHECK", 0.01)
    monkeypatch.setattr(app, "refresh_guide", refresh)
    app.guide_loop(stop)
    assert len(calls) == 2
    assert "guide check failed" in caplog.text


class SlowResolver(FakeResolver):
    def __init__(self, settings: Settings, slow_from: int) -> None:
        super().__init__(settings)
        self.slow_from = slow_from
        self.cancelled = threading.Event()

    def resolve(self, channel: Channel, cancel: threading.Event | None = None) -> Resolved:
        assert cancel is not None
        if self.calls + 1 >= self.slow_from and cancel.wait(30):
            self.calls += 1
            self.cancelled.set()
            raise LookupCancelledError
        return super().resolve(channel, cancel)


def wait_for(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        threading.Event().wait(0.02)
    return condition()


@pytest.mark.parametrize(("ffmpeg_mode", "slow_from"), [("steady", 1), ("short", 2)], ids=["lookup", "reconnect"])
def test_viewer_leaving_before_video_flows_frees_the_tuner(
    make_settings: Callable[..., Settings],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ffmpeg_mode: str,
    slow_from: int,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", ffmpeg_mode)
    settings = make_settings(TUNERS="1")
    relay = Relay()
    resolver = SlowResolver(settings, slow_from)
    ffmpeg = str(fake_ffmpeg(tmp_path))
    guide = Guide(tmp_path / "data" / "guide.xml", 1, downloader=lambda url, path: None)
    app = App(
        settings, ChannelStore(settings.channels_file), guide, lambda channel: Session(channel, resolver, ffmpeg, relay)
    )
    server = Server(("127.0.0.1", 0), app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = socket.create_connection(("127.0.0.1", server.server_address[1]))
        client.sendall(b"GET /stream/101 HTTP/1.1\r\nHost: tellyard\r\n\r\n")
        assert wait_for(lambda: app.streams == 1 and resolver.calls >= slow_from - 1, 5)
        if slow_from > 1:
            assert client.recv(64).startswith(b"HTTP/1.0 200")
            threading.Event().wait(0.5)
        client.close()
        assert wait_for(lambda: app.streams == 0, 3)
        assert resolver.cancelled.is_set()
    finally:
        server.shutdown()
        server.server_close()
        relay.close()


def test_streams_when_the_connection_has_a_high_file_descriptor(base: str) -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = 1200
    if hard != resource.RLIM_INFINITY and hard < wanted:
        pytest.skip("the file descriptor limit is below 1200")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, wanted), hard))
    spare = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]
    try:
        with urllib.request.urlopen(f"{base}/stream/101", timeout=10) as response:
            assert len(response.read(188 * 2000)) == 188 * 2000
    finally:
        for fd in spare:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


def closed_within(client: socket.socket, seconds: float) -> bool:
    client.settimeout(seconds)
    try:
        return client.recv(1024) == b""
    except TimeoutError:
        return False
    except OSError:
        return True


def test_connections_are_capped(app: App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(server, "MAX_CONNECTIONS", 1)
    capped = Server(("127.0.0.1", 0), app)
    threading.Thread(target=capped.serve_forever, daemon=True).start()
    address = ("127.0.0.1", capped.server_address[1])

    def answers() -> bool:
        try:
            return get(f"http://127.0.0.1:{address[1]}/lineup_status.json")[0] == 200
        except OSError:
            return False

    try:
        with socket.create_connection(address) as held:
            with socket.create_connection(address) as refused:
                assert closed_within(refused, 2)
            assert not closed_within(held, 0.3)
        assert "too many connections" in caplog.text
        assert wait_for(answers, 3)
    finally:
        capped.shutdown()
        capped.server_close()


def test_slow_requests_are_dropped(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "REQUEST_DEADLINE", 0.5)
    with socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1]))) as client:
        client.sendall(b"GET /lineup.json HTTP/1.1\r\nHost: t\r\n")
        started = time.monotonic()
        assert closed_within(client, 3)
        assert time.monotonic() - started < 2


def test_oversized_requests_are_refused(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "REQUEST_LIMIT", 4096)
    with socket.create_connection(("127.0.0.1", int(base.rsplit(":", 1)[1]))) as client:
        client.sendall(b"GET /lineup.json HTTP/1.1\r\nHost: t\r\n" + b"X-Big: " + b"x" * 8000 + b"\r\n\r\n")
        client.settimeout(5)
        assert client.recv(64).startswith(b"HTTP/1.0 431")


def test_health_reports_a_guide_that_cannot_be_saved(app: App, tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data").chmod(0o555)
    try:
        app.refresh_guide()
        assert app.guide.data is not None
        problem = app.problem()
        assert problem is not None
        assert problem.startswith("cannot write the guide to ")
    finally:
        (tmp_path / "data").chmod(0o755)
