import queue
from collections.abc import Callable, Iterator, Sequence
from itertools import islice
from pathlib import Path

import pytest

from tellyard import channels, stream
from tellyard.resolve import Resolved
from tellyard.settings import Settings
from tellyard.stream import Process, Session, StreamError, ffmpeg_args
from tests.fakes import FakeResolver, fake_ffmpeg


@pytest.fixture
def channel() -> channels.Channel:
    entry = {"number": 7, "name": "Seven", "logo": "https://e.example.com/l.png", "hls": "https://e.example.com/a"}
    return channels.parse({"channel": [entry]}).channels[0]


@pytest.fixture
def ffmpeg(tmp_path: Path) -> str:
    return str(fake_ffmpeg(tmp_path))


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(stream, "FIRST_DATA_TIMEOUT", 5.0)
    monkeypatch.setattr(stream, "STALL_TIMEOUT", 1.0)
    monkeypatch.setattr(stream, "sleep", lambda seconds: None)
    yield


def test_ffmpeg_args_copy_without_reencoding() -> None:
    resolved = Resolved("https://v.example.com/v.m3u8", None, 1080, (("Referer", "https://r.example.com/"),))
    args = ffmpeg_args("ffmpeg", resolved)
    assert args[args.index("-headers") + 1] == "Referer: https://r.example.com/\r\n"
    assert args[args.index("-c") + 1] == "copy"
    assert args[-3:] == ["-f", "mpegts", "pipe:1"]
    assert "0:a:0?" in args


def test_ffmpeg_args_with_separate_audio_and_custom_agent() -> None:
    resolved = Resolved("https://v.example.com/v.m3u8", "https://v.example.com/a.m3u8", 1080, (("User-Agent", "TV"),))
    args = ffmpeg_args("ffmpeg", resolved)
    assert args.count("-i") == 2
    assert args.count("TV") == 2
    assert "-headers" not in args
    assert "1:a:0" in args


def test_streams_and_stops_ffmpeg(
    make_settings: Callable[..., Settings], channel: channels.Channel, ffmpeg: str
) -> None:
    started: list[Process] = []

    def starter(args: Sequence[str], label: str) -> Process:
        process = Process(args, label)
        started.append(process)
        return process

    with Session(channel, FakeResolver(make_settings()), ffmpeg, starter=starter) as session:
        session.open()
        assert session.height == 1080
        chunks = list(islice(session.chunks(), 5))
    assert all(chunks)
    assert started[0]._process.poll() is not None


def test_reconnects_when_the_source_ends(
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    resolver = FakeResolver(make_settings())
    with Session(channel, resolver, ffmpeg) as session:
        session.open()
        chunks = list(session.chunks())
    # Each attempt yields 3 chunks; quick drops are retried until MAX_ATTEMPTS.
    assert resolver.calls == stream.MAX_ATTEMPTS + 1
    assert len(chunks) == 3 * (stream.MAX_ATTEMPTS + 1)


def test_long_running_source_resets_the_attempt_count(
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    times = iter([0.0, 100.0, 100.0, 200.0, 200.0, 300.0, 300.0, 400.0, 400.0])
    monkeypatch.setattr(stream, "monotonic", lambda: next(times, 1000.0))
    resolver = FakeResolver(make_settings())
    with Session(channel, resolver, ffmpeg) as session:
        session.open()
        list(islice(session.chunks(), 12))
    assert resolver.calls == 4


def test_open_fails_when_the_channel_cannot_resolve(
    make_settings: Callable[..., Settings], channel: channels.Channel, ffmpeg: str
) -> None:
    session = Session(channel, FakeResolver(make_settings(), failures=1), ffmpeg)
    with pytest.raises(StreamError, match="not live"):
        session.open()


def test_open_fails_when_ffmpeg_sends_nothing(
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "fail")
    session = Session(channel, FakeResolver(make_settings()), ffmpeg)
    with pytest.raises(StreamError, match="no video arrived"):
        session.open()


def test_stalled_source_is_restarted(
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = FakeResolver(make_settings())
    reads: Iterator[bytes | Exception] = iter([b"first", queue.Empty(), b"after"])

    class Scripted(Process):
        def __init__(self, args: Sequence[str], label: str) -> None:
            self.closed = False

        def read(self, timeout: float) -> bytes | None:
            item = next(reads, None)
            if isinstance(item, Exception):
                raise item
            return item

        def close(self) -> None:
            self.closed = True

    with Session(channel, resolver, ffmpeg, starter=Scripted) as session:
        session.open()
        chunks = list(islice(session.chunks(), 2))
    assert chunks == [b"first", b"after"]
    assert resolver.calls == 2


def test_missing_ffmpeg_is_reported(make_settings: Callable[..., Settings], channel: channels.Channel) -> None:
    session = Session(channel, FakeResolver(make_settings()), "/nonexistent/ffmpeg")
    with pytest.raises(StreamError, match="is not installed"):
        session.open()
