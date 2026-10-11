import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tellyard.channels import Channel
from tellyard.guide import Guide
from tellyard.server import App, Server
from tellyard.settings import Settings
from tellyard.store import ChannelStore
from tellyard.stream import Session
from tests.fakes import FakeResolver, fake_ffmpeg


@pytest.fixture
def app(make_settings: Callable[..., Settings], channels_file: Path, tmp_path: Path) -> App:
    settings = make_settings(TUNERS="1")
    resolver = FakeResolver(settings)
    ffmpeg = str(fake_ffmpeg(tmp_path))
    guide = Guide(tmp_path / "data" / "guide.xml", 1, downloader=lambda url, path: None)

    def session(channel: Channel) -> Session:
        return Session(channel, resolver, ffmpeg)

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
    data = json.loads(get(f"{base}/health")[2])
    assert data["status"] == "ok"
    assert data["channels"] == 2
    assert data["streams"] == 0


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


def test_failed_guide_rebuild_waits_before_retrying(
    app: App, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[datetime] = []

    def broken(lineup: object, now: datetime) -> None:
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
