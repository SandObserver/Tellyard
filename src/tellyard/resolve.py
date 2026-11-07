import contextlib
import html
import json
import logging
import os
import re
import selectors
import signal
import subprocess
import threading
import urllib.parse
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any, Protocol

from tellyard import egress, hls
from tellyard.channels import Channel, Headers, HlsSource, LiveSource, PageSource
from tellyard.net import NOBODY, Cancel, FetchError, Trust, fetch_text, redact, safe_url, trust_of
from tellyard.settings import Settings

log = logging.getLogger(__name__)
# Also matches JSON with escaped slashes. Must stay linear: pages are untrusted and up to 4 MB.
PAGE_URL = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>]+")
YTDLP_TIMEOUT = 60.0
OUTPUT_LIMIT = 16 * 1024 * 1024
ERROR_TAIL = 64 * 1024
READ_SIZE = 64 * 1024
STREAM_SCAN = 20
FETCH_ATTEMPTS = 3
CANCEL_POLL = 0.5
TWITCH_HOSTS = ("twitch.tv", ".twitch.tv")
# --xff never: without it, yt-dlp sends a made-up address from the allowed country to region-limited sites.
# No config files or plugins: YTDLP_ARGS is the only way to add options.
YTDLP_BASE = ("--ignore-config", "--no-plugin-dirs", "--no-warnings", "--xff", "never")

type Runner = Callable[[Sequence[str], float, threading.Event | None], str]
type Proxy = Callable[[Trust, str], AbstractContextManager[str]]


class Fetcher(Protocol):
    def __call__(
        self, url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None
    ) -> tuple[str, str]: ...


class ResolveError(Exception):
    pass


class LookupCancelledError(ResolveError):
    def __init__(self) -> None:
        super().__init__("the viewer left")


@dataclass(frozen=True)
class Resolved:
    video: str
    audio: str | None
    height: int | None
    headers: Headers
    trust: Trust = NOBODY
    repair_timestamps: bool = False


def is_twitch(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == TWITCH_HOSTS[0] or host.endswith(TWITCH_HOSTS[1])


def kill_group(process: subprocess.Popen[bytes]) -> None:
    # yt-dlp starts Deno. Killing yt-dlp alone leaves Deno running.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def run_command(args: Sequence[str], timeout: float, cancel: threading.Event | None = None) -> str:
    try:
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError as error:
        raise ResolveError(f"{args[0]} is not installed") from error
    with process:
        try:
            stdout, stderr = _collect(process, args[0], timeout, cancel)
        except BaseException:
            # The lookup runs in its own session, so Ctrl-C does not reach it.
            kill_group(process)
            raise
    if process.returncode != 0:
        lines = [line for line in stderr.splitlines() if line.strip()]
        # yt-dlp prints hints after its error line.
        errors = [line for line in lines if line.startswith("ERROR:")] or lines
        raise ResolveError(redact(errors[-1]) if errors else f"{args[0]} exited with code {process.returncode}")
    return stdout


def _collect(
    process: subprocess.Popen[bytes], name: str, timeout: float, cancel: threading.Event | None
) -> tuple[str, str]:
    end = monotonic() + timeout

    def check() -> float:
        if cancel is not None and cancel.is_set():
            raise LookupCancelledError
        left = end - monotonic()
        if left <= 0:
            raise ResolveError(f"{name} took longer than {timeout:.0f} s")
        return min(CANCEL_POLL, left)

    assert process.stdout is not None and process.stderr is not None
    out, err = bytearray(), bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, out)
        selector.register(process.stderr, selectors.EVENT_READ, err)
        while selector.get_map():
            for key, _ in selector.select(check()):
                if chunk := os.read(key.fd, READ_SIZE):
                    key.data.extend(chunk)
                else:
                    selector.unregister(key.fileobj)
            if len(out) > OUTPUT_LIMIT:
                raise ResolveError(f"{name} returned more than {OUTPUT_LIMIT // 1024 // 1024} MB")
            del err[:-ERROR_TAIL]
    while True:
        try:
            process.wait(check())
            break
        except subprocess.TimeoutExpired:
            continue
    return out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


class Resolver:
    def __init__(
        self,
        settings: Settings,
        runner: Runner = run_command,
        fetcher: Fetcher = fetch_text,
        proxy: Proxy = egress.serve,
    ) -> None:
        self._settings = settings
        self._run = runner
        self._fetch = fetcher
        self._proxy = proxy

    def resolve(self, channel: Channel, cancel: Cancel | None = None) -> Resolved:
        try:
            return self._resolve(channel, cancel or Cancel())
        except ResolveError:
            raise
        except Exception as error:
            log.exception("stream lookup failed unexpectedly", extra={"channel": channel.number})
            raise ResolveError(f"the stream lookup failed unexpectedly ({type(error).__name__})") from error

    def _resolve(self, channel: Channel, cancel: Cancel) -> Resolved:
        source, trust = channel.source, trust_of(channel.named_urls, channel.allow)
        match source:
            case HlsSource(token=token) if token:
                return self._pick(self._signed(source, trust, cancel), source.headers, trust, cancel)
            case HlsSource(url=url, headers=headers):
                return self._pick(url, headers, trust, cancel)
            case PageSource():
                return self._pick(self._from_page(source, trust, cancel), source.headers, trust, cancel)
            case LiveSource():
                with self._proxy(trust, channel.number) as proxy:
                    manifest = self._from_ytdlp(self._youtube_target(source, cancel, proxy), cancel, proxy)
                return replace(self._pick(manifest, (), trust, cancel), repair_timestamps=is_twitch(source.url))

    def _signed(self, source: HlsSource, trust: Trust, cancel: Cancel) -> str:
        address = source.token.replace("{url}", urllib.parse.quote(source.url, safe=""))
        try:
            _, text = self._load(address, source.headers, trust, cancel)
            data = json.loads(text)
        except FetchError as error:
            raise ResolveError(f"the token service failed: {error}") from error
        except json.JSONDecodeError as error:
            raise ResolveError("the token service did not return JSON") from error
        wanted = source.token_field.casefold()
        values = data.items() if isinstance(data, dict) else ()
        signed = next((value for key, value in values if isinstance(key, str) and key.casefold() == wanted), None)
        if not isinstance(signed, str) or not signed.startswith(("http://", "https://")):
            raise ResolveError(f"the token service returned no {source.token_field!r} URL")
        return signed

    def _from_page(self, source: PageSource, trust: Trust, cancel: Cancel) -> str:
        try:
            _, text = self._load(source.url, source.headers, trust, cancel)
        except FetchError as error:
            raise ResolveError(f"could not load the channel page: {error}") from error
        for found in PAGE_URL.finditer(text):
            url = html.unescape(found.group()).replace("\\/", "/")
            if ".m3u8" in url.partition("//")[2][1:] and source.match in url:
                return url
        hint = f" containing {source.match!r}" if source.match else ""
        raise ResolveError(f"no playlist URL{hint} on the channel page")

    def _youtube_target(self, source: LiveSource, cancel: threading.Event, proxy: str) -> str:
        if not source.match:
            return source.url
        channel = source.url.rstrip("/").removesuffix("/live")
        args = [self._settings.ytdlp, *YTDLP_BASE, "--flat-playlist", "--playlist-end", str(STREAM_SCAN), "-J"]
        args += [*self._settings.ytdlp_args, "--proxy", proxy, f"{channel}/streams"]
        try:
            listing = _json(self._run(args, YTDLP_TIMEOUT, cancel))
        except LookupCancelledError:
            raise
        except ResolveError as error:
            log.warning(
                "live stream list failed, using the live page", extra={"url": safe_url(channel), "reason": str(error)}
            )
            return source.url
        entries = listing.get("entries")
        wanted = source.match.casefold()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("live_status") != "is_live":
                continue
            title, video = entry.get("title"), entry.get("id")
            if isinstance(title, str) and isinstance(video, str) and wanted in title.casefold():
                return f"https://www.youtube.com/watch?v={video}"
        log.info("no live stream matches, using the live page", extra={"url": safe_url(channel), "match": source.match})
        return source.url

    def _from_ytdlp(self, url: str, cancel: threading.Event, proxy: str) -> str:
        args = [self._settings.ytdlp, *YTDLP_BASE, "--no-playlist", "--skip-download", "-J"]
        args += [*self._settings.ytdlp_args, "--proxy", proxy, url]
        info = _json(self._run(args, YTDLP_TIMEOUT, cancel))
        if info.get("live_status") != "is_live":
            raise ResolveError(f"the channel is not live right now ({info.get('live_status') or 'unknown'})")
        manifest = info.get("manifest_url")
        if not isinstance(manifest, str):
            formats = info.get("formats")
            candidates = formats if isinstance(formats, list) else []
            manifest = next(
                (item["manifest_url"] for item in candidates if isinstance(item, dict) and item.get("manifest_url")),
                None,
            )
        if not isinstance(manifest, str) or not manifest:
            raise ResolveError("yt-dlp returned no HLS playlist for the stream")
        return manifest

    def _pick(self, url: str, headers: Headers, trust: Trust, cancel: Cancel) -> Resolved:
        final, text = self._fetch_with_retry(url, headers, trust, cancel)
        if not hls.is_master(text):
            if not text.lstrip().startswith("#EXTM3U"):
                raise ResolveError("the stream URL did not return an HLS playlist")
            return Resolved(final, None, None, headers, trust)
        try:
            variants, renditions = hls.parse_master(text, final)
        except hls.PlaylistError as error:
            raise ResolveError(str(error)) from error
        choice = hls.choose(variants, renditions)
        return Resolved(choice.video, choice.audio, choice.height, headers, trust)

    def _fetch_with_retry(self, url: str, headers: Headers, trust: Trust, cancel: Cancel) -> tuple[str, str]:
        # Some CDNs hang on some requests. A new connection usually answers.
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                return self._load(url, headers, trust, cancel)
            except FetchError as error:
                if attempt == FETCH_ATTEMPTS:
                    raise ResolveError(f"could not load the playlist: {error}") from error
                log.info("playlist request failed, retrying", extra={"attempt": attempt, "reason": str(error)})
        raise AssertionError("unreachable")

    def _load(self, url: str, headers: Headers, trust: Trust, cancel: Cancel) -> tuple[str, str]:
        if cancel.is_set():
            raise LookupCancelledError
        try:
            return self._fetch(url, headers, trust, cancel=cancel)
        except FetchError:
            if cancel.is_set():
                raise LookupCancelledError from None
            raise


def _json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResolveError("yt-dlp returned output that is not JSON") from error
    if not isinstance(data, dict):
        raise ResolveError("yt-dlp returned an unexpected result")
    return data
