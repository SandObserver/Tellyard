import html
import json
import logging
import re
import subprocess
import threading
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

from tellyard import hls
from tellyard.channels import Channel, Headers, HlsSource, LiveSource, PageSource
from tellyard.net import FetchError, Hosts, fetch_text, redact, safe_url
from tellyard.settings import Settings

log = logging.getLogger(__name__)
# Also matches JSON with escaped slashes. Must stay linear: pages are untrusted and up to 4 MB.
PAGE_URL = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>]+")
YTDLP_TIMEOUT = 60.0
STREAM_SCAN = 20
FETCH_ATTEMPTS = 3
CANCEL_POLL = 0.5
TWITCH_HOSTS = ("twitch.tv", ".twitch.tv")

type Runner = Callable[[Sequence[str], float, threading.Event | None], str]
type Fetcher = Callable[[str, Headers, Hosts], tuple[str, str]]


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
    trusted: Hosts = frozenset()
    repair_timestamps: bool = False


def is_twitch(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == TWITCH_HOSTS[0] or host.endswith(TWITCH_HOSTS[1])


def run_command(args: Sequence[str], timeout: float, cancel: threading.Event | None = None) -> str:
    try:
        process = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except FileNotFoundError as error:
        raise ResolveError(f"{args[0]} is not installed") from error
    end = monotonic() + timeout
    with process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=CANCEL_POLL)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    process.kill()
                    raise LookupCancelledError from None
                if monotonic() > end:
                    process.kill()
                    raise ResolveError(f"{args[0]} took longer than {timeout:.0f} s") from None
    if process.returncode != 0:
        lines = [line for line in stderr.splitlines() if line.strip()]
        raise ResolveError(redact(lines[-1]) if lines else f"{args[0]} exited with code {process.returncode}")
    return stdout


class Resolver:
    def __init__(self, settings: Settings, runner: Runner = run_command, fetcher: Fetcher = fetch_text) -> None:
        self._settings = settings
        self._run = runner
        self._fetch = fetcher

    def resolve(self, channel: Channel, cancel: threading.Event | None = None) -> Resolved:
        try:
            return self._resolve(channel, cancel or threading.Event())
        except ResolveError:
            raise
        except Exception as error:
            log.exception("stream lookup failed unexpectedly", extra={"channel": channel.number})
            raise ResolveError(f"the stream lookup failed unexpectedly ({type(error).__name__})") from error

    def _resolve(self, channel: Channel, cancel: threading.Event) -> Resolved:
        source, trusted = channel.source, channel.hosts
        match source:
            case HlsSource(token=token) if token:
                return self._pick(self._signed(source, trusted), source.headers, trusted, cancel)
            case HlsSource(url=url, headers=headers):
                return self._pick(url, headers, trusted, cancel)
            case PageSource():
                return self._pick(self._from_page(source, trusted), source.headers, trusted, cancel)
            case LiveSource():
                manifest = self._from_ytdlp(self._youtube_target(source, cancel), cancel)
                return replace(self._pick(manifest, (), trusted, cancel), repair_timestamps=is_twitch(source.url))

    def _signed(self, source: HlsSource, trusted: Hosts) -> str:
        address = source.token.replace("{url}", urllib.parse.quote(source.url, safe=""))
        try:
            _, text = self._fetch(address, source.headers, trusted)
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

    def _from_page(self, source: PageSource, trusted: Hosts) -> str:
        try:
            _, text = self._fetch(source.url, source.headers, trusted)
        except FetchError as error:
            raise ResolveError(f"could not load the channel page: {error}") from error
        for found in PAGE_URL.finditer(text):
            url = html.unescape(found.group()).replace("\\/", "/")
            if ".m3u8" in url.partition("//")[2][1:] and source.match in url:
                return url
        hint = f" containing {source.match!r}" if source.match else ""
        raise ResolveError(f"no playlist URL{hint} on the channel page")

    def _youtube_target(self, source: LiveSource, cancel: threading.Event) -> str:
        if not source.match:
            return source.url
        channel = source.url.rstrip("/").removesuffix("/live")
        args = [self._settings.ytdlp, "--no-warnings", "--flat-playlist", "--playlist-end", str(STREAM_SCAN), "-J"]
        args += [*self._settings.ytdlp_args, f"{channel}/streams"]
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

    def _from_ytdlp(self, url: str, cancel: threading.Event) -> str:
        args = [self._settings.ytdlp, "--no-warnings", "--no-playlist", "--skip-download", "-J"]
        args += [*self._settings.ytdlp_args, url]
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

    def _pick(self, url: str, headers: Headers, trusted: Hosts, cancel: threading.Event) -> Resolved:
        final, text = self._fetch_with_retry(url, headers, trusted, cancel)
        if not hls.is_master(text):
            if not text.lstrip().startswith("#EXTM3U"):
                raise ResolveError("the stream URL did not return an HLS playlist")
            return Resolved(final, None, None, headers, trusted)
        try:
            variants, renditions = hls.parse_master(text, final)
        except hls.PlaylistError as error:
            raise ResolveError(str(error)) from error
        choice = hls.choose(variants, renditions)
        return Resolved(choice.video, choice.audio, choice.height, headers, trusted)

    def _fetch_with_retry(self, url: str, headers: Headers, trusted: Hosts, cancel: threading.Event) -> tuple[str, str]:
        # Some CDNs hang on some requests. A new connection usually answers.
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            if cancel.is_set():
                raise LookupCancelledError
            try:
                return self._fetch(url, headers, trusted)
            except FetchError as error:
                if attempt == FETCH_ATTEMPTS:
                    raise ResolveError(f"could not load the playlist: {error}") from error
                log.info("playlist request failed, retrying", extra={"attempt": attempt, "reason": str(error)})
        raise AssertionError("unreachable")


def _json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResolveError("yt-dlp returned output that is not JSON") from error
    if not isinstance(data, dict):
        raise ResolveError("yt-dlp returned an unexpected result")
    return data
