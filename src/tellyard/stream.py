import logging
import os
import queue
import re
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from time import monotonic
from types import TracebackType
from typing import Self

from tellyard.channels import Channel
from tellyard.net import USER_AGENT
from tellyard.resolve import LookupCancelledError, Resolved, ResolveError, Resolver

log = logging.getLogger(__name__)
CHUNK = 188 * 348
QUEUE_CHUNKS = 64
FIRST_DATA_TIMEOUT = 30.0
STALL_TIMEOUT = 20.0
QUICK_DROP = 30.0
MAX_ATTEMPTS = 3
BACKOFF = 2.0
STOP_GRACE = 5.0
CANCEL_POLL = 1.0
READ_TIMEOUT_US = "15000000"
# Stream URLs carry credentials, and signed ones carry tokens in the path and the query.
URL = re.compile(r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?:[^/\s?#@'\"]*@)?(?P<host>[^/\s?#'\"]+)[^\s'\"]*", re.IGNORECASE)


class StreamError(Exception):
    pass


class ViewerLeftError(StreamError):
    pass


def pause(cancel: threading.Event, seconds: float) -> bool:
    return cancel.wait(seconds)


def ffmpeg_args(ffmpeg: str, resolved: Resolved) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]
    args += [*input_options(resolved), "-i", resolved.video]
    if resolved.audio:
        args += [*input_options(resolved), "-i", resolved.audio]
    audio_map = "1:a:0" if resolved.audio else "0:a:0?"
    return [*args, "-map", "0:v:0", "-map", audio_map, "-c", "copy", "-f", "mpegts", "pipe:1"]


def input_options(resolved: Resolved) -> list[str]:
    agent = next((value for key, value in resolved.headers if key.lower() == "user-agent"), USER_AGENT)
    extra = "".join(f"{key}: {value}\r\n" for key, value in resolved.headers if key.lower() != "user-agent")
    args = ["-user_agent", agent]
    if extra:
        args += ["-headers", extra]
    return [*args, "-rw_timeout", READ_TIMEOUT_US]


class Process:
    def __init__(self, args: Sequence[str], label: str) -> None:
        self._label = label
        self._closed = threading.Event()
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=QUEUE_CHUNKS)
        try:
            self._process = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
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
            self._process.terminate()
            try:
                self._process.wait(STOP_GRACE)
            except subprocess.TimeoutExpired:
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
                text = URL.sub(r"\g<scheme>\g<host>", line.decode("utf-8", errors="replace").strip())
                if text:
                    # After close, ffmpeg reports the closed pipe. These errors are expected.
                    level = logging.DEBUG if self._closed.is_set() else logging.WARNING
                    log.log(level, "ffmpeg", extra={"channel": self._label, "output": text})


type Starter = Callable[[Sequence[str], str], Process]


class Session:
    def __init__(self, channel: Channel, resolver: Resolver, ffmpeg: str, *, starter: Starter = Process) -> None:
        self.channel = channel
        self._resolver = resolver
        self._ffmpeg = ffmpeg
        self._starter = starter
        self._process: Process | None = None
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
        process = self._starter(ffmpeg_args(self._ffmpeg, resolved), self.label)
        try:
            first = self._read(process, FIRST_DATA_TIMEOUT)
        except queue.Empty:
            first = None
        if self.cancel.is_set():
            process.close()
            raise ViewerLeftError("the viewer left")
        if not first:
            process.close()
            raise StreamError(f"no video arrived within {FIRST_DATA_TIMEOUT:.0f} s")
        self._process = process
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
