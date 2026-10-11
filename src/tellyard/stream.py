"""One viewer's stream: ffmpeg remuxes the source to MPEG-TS and is restarted when the source drops."""

import logging
import os
import queue
import subprocess
import threading
from collections.abc import Callable, Iterator, Sequence
from time import monotonic, sleep
from types import TracebackType
from typing import Self

from tellyard.channels import Channel
from tellyard.net import USER_AGENT
from tellyard.resolve import Resolved, ResolveError, Resolver

log = logging.getLogger(__name__)
CHUNK = 188 * 348
QUEUE_CHUNKS = 64
FIRST_DATA_TIMEOUT = 30.0
STALL_TIMEOUT = 20.0
# A source that ends sooner than this after starting counts as a failed attempt.
QUICK_DROP = 30.0
MAX_ATTEMPTS = 3
BACKOFF = 2.0
STOP_GRACE = 5.0
READ_TIMEOUT_US = "15000000"


class StreamError(Exception):
    pass


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
    """ffmpeg with its output pumped into a bounded queue so reads can time out."""

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
        """The next chunk, or None once ffmpeg has finished. Raises queue.Empty on timeout."""
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
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    # Once Tellyard stops ffmpeg, its complaints about the closed pipe are expected.
                    level = logging.DEBUG if self._closed.is_set() else logging.WARNING
                    log.log(level, "%s: ffmpeg: %s", self._label, text)


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

    @property
    def label(self) -> str:
        return f"{self.channel.number} {self.channel.name}"

    def open(self) -> None:
        """Resolve and start the stream, returning only once video is flowing."""
        self._start()

    def chunks(self) -> Iterator[bytes]:
        while True:
            if self._pending is not None:
                chunk, self._pending = self._pending, None
                yield chunk
            assert self._process is not None
            try:
                chunk_or_end = self._process.read(STALL_TIMEOUT)
            except queue.Empty:
                log.warning("%s: no data for %.0f s, restarting", self.label, STALL_TIMEOUT)
                chunk_or_end = None
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
            resolved = self._resolver.resolve(self.channel)
        except ResolveError as error:
            raise StreamError(str(error)) from error
        self.height = resolved.height
        process = self._starter(ffmpeg_args(self._ffmpeg, resolved), self.label)
        try:
            first = process.read(FIRST_DATA_TIMEOUT)
        except queue.Empty:
            first = None
        if not first:
            process.close()
            raise StreamError(f"no video arrived within {FIRST_DATA_TIMEOUT:.0f} s")
        self._process = process
        self._pending = first
        self._started = monotonic()

    def _restart(self) -> bool:
        ran = monotonic() - self._started
        self.close()
        self._attempts = self._attempts + 1 if ran < QUICK_DROP else 1
        while self._attempts <= MAX_ATTEMPTS:
            if self._attempts > 1:
                sleep(BACKOFF * (self._attempts - 1))
            log.info("%s: source ended after %.0f s, reconnecting (attempt %d)", self.label, ran, self._attempts)
            try:
                self._start()
            except StreamError as error:
                log.warning("%s: reconnect failed: %s", self.label, error)
                self._attempts += 1
            else:
                return True
        log.error("%s: giving up after %d failed attempts", self.label, MAX_ATTEMPTS)
        return False
