import html
import json
import logging
import re
import subprocess
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tellyard import hls
from tellyard.channels import DEFAULT_PAGE_PATTERN, Channel, Headers, HlsSource, PageSource, YouTubeSource
from tellyard.net import FetchError, fetch_text
from tellyard.settings import Settings

log = logging.getLogger(__name__)
PAGE_PATTERN = re.compile(DEFAULT_PAGE_PATTERN)
YTDLP_TIMEOUT = 60.0
STREAM_SCAN = 20
FETCH_ATTEMPTS = 3

type Runner = Callable[[Sequence[str], float], str]
type Fetcher = Callable[[str, Headers], tuple[str, str]]


class ResolveError(Exception):
    pass


@dataclass(frozen=True)
class Resolved:
    video: str
    audio: str | None
    height: int | None
    headers: Headers


def run_command(args: Sequence[str], timeout: float) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as error:
        raise ResolveError(f"{args[0]} is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise ResolveError(f"{args[0]} took longer than {timeout:.0f} s") from error
    if done.returncode != 0:
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        raise ResolveError(lines[-1] if lines else f"{args[0]} exited with code {done.returncode}")
    return done.stdout


class Resolver:
    def __init__(self, settings: Settings, runner: Runner = run_command, fetcher: Fetcher = fetch_text) -> None:
        self._settings = settings
        self._run = runner
        self._fetch = fetcher

    def resolve(self, channel: Channel) -> Resolved:
        source = channel.source
        match source:
            case HlsSource(token=token) if token:
                return self._pick(self._signed(source), source.headers)
            case HlsSource(url=url, headers=headers):
                return self._pick(url, headers)
            case PageSource():
                return self._pick(self._from_page(source), source.headers)
            case YouTubeSource():
                return self._pick(self._from_youtube(self._youtube_target(source)), ())

    def _signed(self, source: HlsSource) -> str:
        address = source.token.replace("{url}", urllib.parse.quote(source.url, safe=""))
        try:
            _, text = self._fetch(address, source.headers)
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

    def _from_page(self, source: PageSource) -> str:
        try:
            _, text = self._fetch(source.url, source.headers)
        except FetchError as error:
            raise ResolveError(f"could not load the channel page: {error}") from error
        for found in PAGE_PATTERN.findall(text):
            url = html.unescape(found).replace("\\/", "/")
            if source.match in url:
                return url
        hint = f" containing {source.match!r}" if source.match else ""
        raise ResolveError(f"no playlist URL{hint} on the channel page")

    def _youtube_target(self, source: YouTubeSource) -> str:
        if not source.match:
            return source.url
        channel = source.url.rstrip("/").removesuffix("/live")
        args = [self._settings.ytdlp, "--no-warnings", "--flat-playlist", "--playlist-end", str(STREAM_SCAN), "-J"]
        args += [*self._settings.ytdlp_args, f"{channel}/streams"]
        try:
            listing = _json(self._run(args, YTDLP_TIMEOUT))
        except ResolveError as error:
            log.warning("could not list the live streams of %s, using its live page: %s", channel, error)
            return source.url
        entries = listing.get("entries")
        wanted = source.match.casefold()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get("live_status") != "is_live":
                continue
            title, video = entry.get("title"), entry.get("id")
            if isinstance(title, str) and isinstance(video, str) and wanted in title.casefold():
                return f"https://www.youtube.com/watch?v={video}"
        log.info("no live stream titled %r on %s, using its live page", source.match, channel)
        return source.url

    def _from_youtube(self, url: str) -> str:
        args = [self._settings.ytdlp, "--no-warnings", "--no-playlist", "--skip-download", "-J"]
        args += [*self._settings.ytdlp_args, url]
        info = _json(self._run(args, YTDLP_TIMEOUT))
        if info.get("live_status") != "is_live":
            raise ResolveError(f"the YouTube channel is not live right now ({info.get('live_status') or 'unknown'})")
        manifest = info.get("manifest_url")
        if not isinstance(manifest, str):
            formats = info.get("formats")
            candidates = formats if isinstance(formats, list) else []
            manifest = next(
                (item["manifest_url"] for item in candidates if isinstance(item, dict) and item.get("manifest_url")),
                None,
            )
        if not isinstance(manifest, str) or not manifest:
            raise ResolveError("YouTube returned no HLS playlist for the stream")
        return manifest

    def _pick(self, url: str, headers: Headers) -> Resolved:
        final, text = self._fetch_with_retry(url, headers)
        if not hls.is_master(text):
            if not text.lstrip().startswith("#EXTM3U"):
                raise ResolveError("the stream URL did not return an HLS playlist")
            return Resolved(final, None, None, headers)
        try:
            variants, renditions = hls.parse_master(text, final)
        except hls.PlaylistError as error:
            raise ResolveError(str(error)) from error
        choice = hls.choose(variants, renditions)
        return Resolved(choice.video, choice.audio, choice.height, headers)

    def _fetch_with_retry(self, url: str, headers: Headers) -> tuple[str, str]:
        # Some CDNs hang on some requests. A new connection usually answers.
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                return self._fetch(url, headers)
            except FetchError as error:
                if attempt == FETCH_ATTEMPTS:
                    raise ResolveError(f"could not load the playlist: {error}") from error
                log.info("playlist request failed, retrying: %s", error)
        raise AssertionError("unreachable")


def _json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResolveError("yt-dlp returned output that is not JSON") from error
    if not isinstance(data, dict):
        raise ResolveError("yt-dlp returned an unexpected result")
    return data
