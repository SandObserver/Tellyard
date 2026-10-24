import logging
import queue
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from itertools import islice
from pathlib import Path
from time import monotonic

import pytest

from tellyard import channels, stream
from tellyard.relay import Relay
from tellyard.resolve import Resolved, ResolveError
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
    monkeypatch.setattr(stream, "pause", lambda cancel, seconds: cancel.is_set())
    yield


def test_ffmpeg_args_copy_without_reencoding() -> None:
    resolved = Resolved("http://127.0.0.1:9/t/0/v.m3u8", None, 1080, ())
    args = ffmpeg_args("ffmpeg", resolved)
    assert args[args.index("-c") + 1] == "copy"
    assert args[-3:] == ["-f", "mpegts", "pipe:1"]
    assert "0:a:0?" in args
    assert "-bsf:v" not in args
    assert "-headers" not in args


def test_ffmpeg_args_repair_timestamps_when_asked() -> None:
    resolved = Resolved("http://127.0.0.1:9/t/0/v.m3u8", None, 1080, (), repair_timestamps=True)
    args = ffmpeg_args("ffmpeg", resolved)
    assert args[args.index("-bsf:v") + 1].startswith("setts=")
    assert args[-3:] == ["-f", "mpegts", "pipe:1"]


def test_ffmpeg_args_with_separate_audio() -> None:
    resolved = Resolved("http://127.0.0.1:9/t/0/v.m3u8", "http://127.0.0.1:9/t/1/a.m3u8", 1080, ())
    args = ffmpeg_args("ffmpeg", resolved)
    assert args.count("-i") == 2
    assert "1:a:0" in args


def test_ffmpeg_reads_only_through_the_relay(relay: Relay) -> None:
    headers = (("Referer", "https://r.example.com/"), ("Cookie", "secret"))
    resolved = Resolved("https://v.example.com/v.m3u8", "https://v.example.com/a.m3u8", 1080, headers)
    feed = relay.open(resolved, "7 Seven")
    args = ffmpeg_args("ffmpeg", stream.local(resolved, feed))
    inputs = [args[index + 1] for index, arg in enumerate(args) if arg == "-i"]
    assert inputs == [feed.video, feed.audio]
    assert all(value.startswith(f"{relay.base}/") for value in inputs)
    assert not any("secret" in arg or "example.com" in arg for arg in args)


def test_streams_and_stops_ffmpeg(
    relay: Relay, make_settings: Callable[..., Settings], channel: channels.Channel, ffmpeg: str
) -> None:
    started: list[Process] = []

    def starter(args: Sequence[str], label: str) -> Process:
        process = Process(args, label)
        started.append(process)
        return process

    with Session(channel, FakeResolver(make_settings()), ffmpeg, relay, starter=starter) as session:
        session.open()
        assert session.height == 1080
        chunks = list(islice(session.chunks(), 5))
    assert all(chunks)
    assert started[0]._process.poll() is not None


def test_reconnects_when_the_source_ends(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    resolver = FakeResolver(make_settings())
    with Session(channel, resolver, ffmpeg, relay) as session:
        session.open()
        chunks = list(session.chunks())
    # Each attempt yields 3 chunks; quick drops are retried until MAX_ATTEMPTS.
    assert resolver.calls == stream.MAX_ATTEMPTS + 1
    assert len(chunks) == 3 * (stream.MAX_ATTEMPTS + 1)


def test_long_running_source_resets_the_attempt_count(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    monkeypatch.setattr(stream, "QUICK_DROP", 0.0)
    resolver = FakeResolver(make_settings())
    with Session(channel, resolver, ffmpeg, relay) as session:
        session.open()
        list(islice(session.chunks(), 15))
    assert resolver.calls == 5


def test_open_fails_when_the_channel_cannot_resolve(
    relay: Relay, make_settings: Callable[..., Settings], channel: channels.Channel, ffmpeg: str
) -> None:
    session = Session(channel, FakeResolver(make_settings(), failures=1), ffmpeg, relay)
    with pytest.raises(StreamError, match="not live"):
        session.open()


def test_open_fails_when_ffmpeg_sends_nothing(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "fail")
    session = Session(channel, FakeResolver(make_settings()), ffmpeg, relay)
    with pytest.raises(StreamError, match="no video arrived"):
        session.open()


def test_stalled_source_is_restarted(
    relay: Relay,
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
                threading.Event().wait(timeout)
                raise item
            return item

        def close(self) -> None:
            self.closed = True

    with Session(channel, resolver, ffmpeg, relay, starter=Scripted) as session:
        session.open()
        chunks = list(islice(session.chunks(), 2))
    assert chunks == [b"first", b"after"]
    assert resolver.calls == 2


def test_missing_ffmpeg_is_reported(
    relay: Relay, make_settings: Callable[..., Settings], channel: channels.Channel
) -> None:
    session = Session(channel, FakeResolver(make_settings()), "/nonexistent/ffmpeg", relay)
    with pytest.raises(StreamError, match="is not installed"):
        session.open()


def test_gives_up_when_reconnects_fail(
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    waits: list[float] = []

    def pause(cancel: threading.Event, seconds: float) -> bool:
        waits.append(seconds)
        return False

    monkeypatch.setattr(stream, "pause", pause)

    class Once(FakeResolver):
        def resolve(self, channel: channels.Channel, cancel: threading.Event | None = None) -> Resolved:
            if self.calls:
                self.calls += 1
                raise ResolveError("the YouTube channel is not live right now")
            return super().resolve(channel, cancel)

    resolver = Once(make_settings())
    with closing(Relay()) as relay, Session(channel, resolver, ffmpeg, relay) as session:
        session.open()
        chunks = list(session.chunks())
    assert len(chunks) == 3
    assert resolver.calls == 1 + stream.MAX_ATTEMPTS
    assert waits == [2.0, 4.0]
    assert "stream given up" in caplog.text


def test_ffmpeg_output_is_logged_without_url_paths(caplog: pytest.LogCaptureFixture) -> None:
    line = "Error opening input file https://user:PASSWORD@cdn.example.com/live/index.m3u8?token=SECRET&e=9."
    process = Process(["sh", "-c", f"echo '{line}' >&2"], "7 Seven")
    with caplog.at_level(logging.WARNING):
        assert process.read(5) is None
        deadline = monotonic() + 5
        while not caplog.records and monotonic() < deadline:
            threading.Event().wait(0.01)
        process.close()
    outputs = [getattr(record, "output", "") for record in caplog.records]
    assert "Error opening input file https://cdn.example.com" in outputs
    assert "SECRET" not in caplog.text
    assert "PASSWORD" not in caplog.text


def test_close_signals_twice_when_ffmpeg_finishes_its_read_first() -> None:
    process = Process(["sh", "-c", "trap 'trap - TERM' TERM; echo ready; while :; do sleep 0.05; done"], "7 Seven")
    assert process.read(5) == b"ready\n"
    began = monotonic()
    process.close()
    assert monotonic() - began < stream.STOP_GRACE / 2


def test_leaving_during_the_first_data_wait_stops_ffmpeg(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "silent")
    monkeypatch.setattr(stream, "FIRST_DATA_TIMEOUT", 30.0)
    started: list[Process] = []

    def starter(args: Sequence[str], label: str) -> Process:
        started.append(Process(args, label))
        return started[-1]

    session = Session(channel, FakeResolver(make_settings()), ffmpeg, relay, starter=starter)
    threading.Timer(0.3, session.cancel.set).start()
    began = monotonic()
    with pytest.raises(stream.ViewerLeftError):
        session.open()
    assert monotonic() - began < 3
    assert started[0]._process.poll() is not None


def test_leaving_during_a_stall_skips_the_reconnect(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stream, "STALL_TIMEOUT", 30.0)
    resolver = FakeResolver(make_settings())

    class Stalls(Process):
        def __init__(self, args: Sequence[str], label: str) -> None:
            self.first = True

        def read(self, timeout: float) -> bytes | None:
            if self.first:
                self.first = False
                return b"first"
            threading.Event().wait(timeout)
            raise queue.Empty

        def close(self) -> None:
            pass

    with Session(channel, resolver, ffmpeg, relay, starter=Stalls) as session:
        session.open()
        threading.Timer(0.3, session.cancel.set).start()
        began = monotonic()
        assert list(session.chunks()) == [b"first"]
    assert monotonic() - began < 3
    assert resolver.calls == 1


def test_leaving_during_the_backoff_skips_the_reconnect(
    relay: Relay,
    make_settings: Callable[..., Settings],
    channel: channels.Channel,
    ffmpeg: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_FFMPEG", "short")

    def pause(cancel: threading.Event, seconds: float) -> bool:
        cancel.set()
        return True

    monkeypatch.setattr(stream, "pause", pause)

    class Once(FakeResolver):
        def resolve(self, channel: channels.Channel, cancel: threading.Event | None = None) -> Resolved:
            if self.calls:
                self.calls += 1
                raise ResolveError("the YouTube channel is not live right now")
            return super().resolve(channel, cancel)

    resolver = Once(make_settings())
    with Session(channel, resolver, ffmpeg, relay) as session:
        session.open()
        list(session.chunks())
    assert resolver.calls == 2
