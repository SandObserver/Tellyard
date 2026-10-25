import logging
import os
import queue
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from time import monotonic
from types import TracebackType
from typing import Self

from tellyard.channels import Channel
from tellyard.net import redact
from tellyard.relay import Feed, Relay
from tellyard.resolve import LookupCancelledError, Resolved, ResolveError, Resolver

log = logging.getLogger(__name__)
CHUNK = 188 * 348
QUEUE_CHUNKS = 64
FIRST_DATA_TIMEOUT = 30.0
STALL_TIMEOUT = 20.0
QUICK_DROP = 30.0
MAX_ATTEMPTS = 3
BACKOFF = 2.0
SECOND_SIGNAL = 0.2
STOP_GRACE = 5.0
CANCEL_POLL = 1.0
READ_TIMEOUT_US = "15000000"
PROXY_VARIABLES = frozenset({"http_proxy", "https_proxy", "all_proxy"})
# Twitch ad breaks hand over frames stamped with the stream's uptime. Re-encoding players freeze on them.
TIMESTAMP_REPAIR = (
    "setts=pts='if(gt(N,0)*gt(abs(PTS-PREV_OUTPTS)*TB,10),PREV_OUTPTS+1/(30*TB),PTS)'"
    ":dts='if(gt(N,0)*gt(abs(DTS-PREV_OUTDTS)*TB,10),PREV_OUTDTS+1/(30*TB),DTS)'"
)


class StreamError(Exception):
    pass


class ViewerLeftError(StreamError):
    pass


def pause(cancel: threading.Event, seconds: float) -> bool:
    return cancel.wait(seconds)


def ffmpeg_args(ffmpeg: str, resolved: Resolved) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
    args += [*input_options(), "-i", resolved.video]
    if resolved.audio:
        args += [*input_options(), "-i", resolved.audio]
    audio_map = "1:a:0" if resolved.audio else "0:a:0?"
    args += ["-map", "0:v:0", "-map", audio_map, "-c", "copy"]
    if resolved.repair_timestamps:
        args += ["-bsf:v", TIMESTAMP_REPAIR]
    return [*args, "-f", "mpegts", "pipe:1"]


def input_options() -> list[str]:
    return ["-rw_timeout", READ_TIMEOUT_US]


def ffmpeg_env() -> dict[str, str]:
    # ffmpeg reads only the relay on 127.0.0.1. A proxy cannot reach that address.
    return {key: value for key, value in os.environ.items() if key.lower() not in PROXY_VARIABLES}


def local(resolved: Resolved, feed: Feed) -> Resolved:
    # ffmpeg reads the source only through the relay, which checks every address it requests.
    return replace(resolved, video=feed.video, audio=feed.audio, headers=())


class Process:
    def __init__(self, args: Sequence[str], label: str) -> None:
        self._label = label
        self._closed = threading.Event()
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=QUEUE_CHUNKS)
        try:
            self._process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ffmpeg_env()
            )
        except FileNotFoundError as error:
            raise StreamError(f"{args[0]} is not installed") from error
        threading.Thread(target=self._pump, name=f"pump {label}", daemon=True).start()
        threading.Thread(target=self._drain, name=f"log {label}", daemon=True).start()

    def read(self, timeout: float) -> bytes | None:
        return self._queue.get(timeout=timeout)

    def close(self) -> None:
        self._closed.set()
        if self._process.poll() is None:
            # On the first signal ffmpeg waits for its current read, up to a segment. The second interrupts the read.
            for grace in (SECOND_SIGNAL, STOP_GRACE):
                self._process.terminate()
                try:
                    self._process.wait(grace)
                except subprocess.TimeoutExpired:
                    continue
                return
            self._process.kill()
            self._process.wait()

    def _pump(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        with stdout:
            while chunk := os.read(stdout.fileno(), CHUNK):
                if not self._put(chunk):
                    return
        self._put(None)

    def _put(self, item: bytes | None) -> bool:
        while not self._closed.is_set():
            try:
                self._queue.put(item, timeout=0.5)
            except queue.Full:
                continue
            return True
        return False

    def _drain(self) -> None:
        stderr = self._process.stderr
        assert stderr is not None
        with stderr:
            for line in stderr:
                text = redact(line.decode("utf-8", errors="replace").strip())
                if text:
                    # After close, ffmpeg reports the closed pipe. These errors are expected.
                    level = logging.DEBUG if self._closed.is_set() else logging.WARNING
                    log.log(level, "ffmpeg", extra={"channel": self._label, "output": text})


type Starter = Callable[[Sequence[str], str], Process]


class Session:
    def __init__(
        self, channel: Channel, resolver: Resolver, ffmpeg: str, relay: Relay, *, starter: Starter = Process
    ) -> None:
        self.channel = channel
        self._resolver = resolver
        self._ffmpeg = ffmpeg
        self._relay = relay
        self._starter = starter
        self._process: Process | None = None
        self._feed: Feed | None = None
        self._pending: bytes | None = None
        self._started = 0.0
        self._attempts = 0
        self.height: int | None = None
        self.cancel = threading.Event()

    @property
    def label(self) -> str:
        return f"{self.channel.number} {self.channel.name}"

    @property
    def fields(self) -> dict[str, str]:
        return {"channel": self.channel.number, "title": self.channel.name}

    def open(self) -> None:
        self._start()

    def chunks(self) -> Iterator[bytes]:
        while not self.cancel.is_set():
            if self._pending is not None:
                chunk, self._pending = self._pending, None
                yield chunk
            assert self._process is not None
            try:
                chunk_or_end = self._read(self._process, STALL_TIMEOUT)
            except queue.Empty:
                log.warning("no data, restarting", extra={**self.fields, "seconds": round(STALL_TIMEOUT)})
                chunk_or_end = None
            if self.cancel.is_set():
                return
            if chunk_or_end:
                yield chunk_or_end
            elif not self._restart():
                return

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None
        if self._feed is not None:
            self._feed.close()
            self._feed = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.close()

    def _start(self) -> None:
        try:
            resolved = self._resolver.resolve(self.channel, self.cancel)
        except LookupCancelledError as error:
            raise ViewerLeftError(str(error)) from error
        except ResolveError as error:
            raise StreamError(str(error)) from error
        self.height = resolved.height
        feed = self._relay.open(resolved, self.label)
        try:
            process = self._starter(ffmpeg_args(self._ffmpeg, local(resolved, feed)), self.label)
        except BaseException:
            feed.close()
            raise
        try:
            first = self._read(process, FIRST_DATA_TIMEOUT)
        except queue.Empty:
            first = None
        if self.cancel.is_set() or not first:
            process.close()
            feed.close()
            if self.cancel.is_set():
                raise ViewerLeftError("the viewer left")
            raise StreamError(f"no video arrived within {FIRST_DATA_TIMEOUT:.0f} s")
        self._process = process
        self._feed = feed
        self._pending = first
        self._started = monotonic()

    def _read(self, process: Process, timeout: float) -> bytes | None:
        end = monotonic() + timeout
        while not self.cancel.is_set():
            left = end - monotonic()
            if left <= 0:
                raise queue.Empty
            try:
                return process.read(min(CANCEL_POLL, left))
            except queue.Empty:
                continue
        return None

    def _restart(self) -> bool:
        ran = monotonic() - self._started
        self.close()
        if self.cancel.is_set():
            return False
        self._attempts = self._attempts + 1 if ran < QUICK_DROP else 1
        while self._attempts <= MAX_ATTEMPTS:
            if self._attempts > 1 and pause(self.cancel, BACKOFF * (self._attempts - 1)):
                return False
            log.info(
                "source ended, reconnecting", extra={**self.fields, "after_s": round(ran), "attempt": self._attempts}
            )
            try:
                self._start()
            except ViewerLeftError:
                return False
            except StreamError as error:
                log.warning("reconnect failed", extra={**self.fields, "reason": str(error)})
                self._attempts += 1
            else:
                return True
        log.error("stream given up", extra={**self.fields, "attempts": MAX_ATTEMPTS})
        return False
