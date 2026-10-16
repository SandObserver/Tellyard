import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Sequence

import pytest

from tellyard import channels
from tellyard.channels import Channel, Headers
from tellyard.net import FetchError
from tellyard.resolve import LookupCancelledError, Resolved, ResolveError, Resolver, Runner, run_command
from tellyard.settings import Settings

MASTER = (
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720\n720.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1920x1080\n1080.m3u8\n"
)


def channel(**source: object) -> Channel:
    entry = {"number": 1, "name": "Test", "logo": "https://example.com/l.png", **source}
    return channels.parse({"channel": [entry]}).channels[0]


def pages(mapping: dict[str, str]) -> Callable[[str, Headers], tuple[str, str]]:
    def fetch(url: str, headers: Headers) -> tuple[str, str]:
        if url not in mapping:
            raise FetchError(f"{url}: HTTP 404")
        return url, mapping[url]

    return fetch


def runner_returning(output: str, seen: list[Sequence[str]] | None = None) -> Runner:
    def run(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        if seen is not None:
            seen.append(args)
        return output

    return run


def test_hls_source_picks_the_best_variant(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://cdn.example.com/live/master.m3u8": MASTER})
    resolver = Resolver(make_settings(), fetcher=fetch)
    resolved = resolver.resolve(channel(hls="https://cdn.example.com/live/master.m3u8"))
    assert resolved == Resolved("https://cdn.example.com/live/1080.m3u8", None, 1080, ())


def test_media_playlist_is_used_as_is(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://cdn.example.com/a.m3u8": "#EXTM3U\n#EXTINF:6,\nseg.ts\n"})
    resolved = Resolver(make_settings(), fetcher=fetch).resolve(channel(hls="https://cdn.example.com/a.m3u8"))
    assert resolved.video == "https://cdn.example.com/a.m3u8"
    assert resolved.height is None


def test_rejects_a_non_playlist(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://cdn.example.com/a.m3u8": "<html>blocked</html>"})
    with pytest.raises(ResolveError, match="did not return an HLS playlist"):
        Resolver(make_settings(), fetcher=fetch).resolve(channel(hls="https://cdn.example.com/a.m3u8"))


def test_retries_a_hanging_playlist_request(make_settings: Callable[..., Settings]) -> None:
    attempts: list[str] = []

    def flaky(url: str, headers: Headers) -> tuple[str, str]:
        attempts.append(url)
        if len(attempts) < 3:
            raise FetchError("cdn.example.com: The read operation timed out")
        return url, MASTER

    resolved = Resolver(make_settings(), fetcher=flaky).resolve(channel(hls="https://cdn.example.com/m.m3u8"))
    assert resolved.height == 1080
    assert len(attempts) == 3


def test_gives_up_after_three_playlist_attempts(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ResolveError, match="could not load the playlist"):
        Resolver(make_settings(), fetcher=pages({})).resolve(channel(hls="https://cdn.example.com/m.m3u8"))


def test_page_source_finds_escaped_urls(make_settings: Callable[..., Settings]) -> None:
    page = (
        '<script>var a = "https://ads.example.com/ad.m3u8";'
        ' var b = {"src":"https:\\/\\/cdn.example.com\\/live\\/master.m3u8?app=web&amp;ce=3"};</script>'
    )
    fetch = pages(
        {"https://tv.example.com/live": page, "https://cdn.example.com/live/master.m3u8?app=web&ce=3": MASTER}
    )
    resolver = Resolver(make_settings(), fetcher=fetch)
    resolved = resolver.resolve(channel(page="https://tv.example.com/live", match="cdn.example.com"))
    assert resolved.video == "https://cdn.example.com/live/1080.m3u8"


def test_page_source_without_a_match(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://tv.example.com/live": "<p>no stream here</p>"})
    with pytest.raises(ResolveError, match="no playlist URL containing 'cdn'"):
        Resolver(make_settings(), fetcher=fetch).resolve(channel(page="https://tv.example.com/live", match="cdn"))


def test_page_that_fails_to_load(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ResolveError, match="could not load the channel page"):
        Resolver(make_settings(), fetcher=pages({})).resolve(channel(page="https://tv.example.com/live"))


def test_page_scan_takes_linear_time(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://tv.example.com/live": "https://" * 500_000})
    started = time.monotonic()
    with pytest.raises(ResolveError, match="no playlist URL"):
        Resolver(make_settings(), fetcher=fetch).resolve(channel(page="https://tv.example.com/live"))
    assert time.monotonic() - started < 2


def test_page_url_needs_a_host_before_the_playlist(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://tv.example.com/live": "<a href='https://.m3u8'>"})
    with pytest.raises(ResolveError, match="no playlist URL"):
        Resolver(make_settings(), fetcher=fetch).resolve(channel(page="https://tv.example.com/live"))


def test_unexpected_lookup_errors_become_resolve_errors(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    def broken(url: str, headers: Headers) -> tuple[str, str]:
        raise ValueError("bad value")

    with pytest.raises(ResolveError, match="failed unexpectedly"):
        Resolver(make_settings(), fetcher=broken).resolve(channel(hls="https://cdn.example.com/m.m3u8"))
    assert "stream lookup failed unexpectedly" in caplog.text


def test_youtube_uses_the_live_manifest(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    info = json.dumps({"live_status": "is_live", "manifest_url": "https://manifest.googlevideo.com/master.m3u8"})
    fetch = pages({"https://manifest.googlevideo.com/master.m3u8": MASTER})
    settings = make_settings(YTDLP="/opt/yt-dlp", YTDLP_ARGS="--proxy http://p:1")
    resolver = Resolver(settings, runner=runner_returning(info, seen), fetcher=fetch)
    resolved = resolver.resolve(channel(youtube="https://www.youtube.com/@news/live"))
    assert resolved.height == 1080
    assert seen[0][0] == "/opt/yt-dlp"
    assert seen[0][-3:] == ["--proxy", "http://p:1", "https://www.youtube.com/@news/live"]


def test_youtube_falls_back_to_format_manifests(make_settings: Callable[..., Settings]) -> None:
    info = json.dumps(
        {"live_status": "is_live", "formats": [{"format_id": "sb"}, {"manifest_url": "https://m.example.com/x.m3u8"}]}
    )
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(make_settings(), runner=runner_returning(info), fetcher=fetch)
    assert resolver.resolve(channel(youtube="https://www.youtube.com/@n/live")).height == 1080


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (json.dumps({"live_status": "was_live"}), "not live right now \\(was_live\\)"),
        (json.dumps({"live_status": "is_live", "formats": []}), "no HLS playlist"),
        ("not json", "not JSON"),
        ("[]", "unexpected result"),
    ],
)
def test_youtube_problems(make_settings: Callable[..., Settings], output: str, message: str) -> None:
    resolver = Resolver(make_settings(), runner=runner_returning(output), fetcher=pages({}))
    with pytest.raises(ResolveError, match=message):
        resolver.resolve(channel(youtube="https://www.youtube.com/@n/live"))


def test_run_command_reports_failures() -> None:
    assert run_command([sys.executable, "-c", "print('hi')"], 10) == "hi\n"
    with pytest.raises(ResolveError, match="boom"):
        run_command([sys.executable, "-c", "import sys; sys.stderr.write('x\\nboom\\n'); sys.exit(3)"], 10)
    with pytest.raises(ResolveError, match="exited with code 4"):
        run_command([sys.executable, "-c", "import sys; sys.exit(4)"], 10)
    with pytest.raises(ResolveError, match="took longer"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.2)
    with pytest.raises(ResolveError, match="is not installed"):
        run_command(["/nonexistent/yt-dlp"], 10)


def scripted(outputs: dict[str, str], seen: list[Sequence[str]]) -> Runner:
    def run(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        seen.append(args)
        target = args[-1]
        if target not in outputs:
            raise ResolveError(f"no answer for {target}")
        return outputs[target]

    return run


LIVE = json.dumps({"live_status": "is_live", "manifest_url": "https://m.example.com/x.m3u8"})
STREAMS = json.dumps(
    {
        "entries": [
            {"id": "old", "title": "LiveNOW 24/7 yesterday", "live_status": "was_live"},
            {"id": "top", "title": "TOP STORIES: today", "live_status": "is_live"},
            {"id": "main", "title": "LIVE NEWS: LiveNOW FOX 24/7 LIVE STREAM", "live_status": "is_live"},
            "junk",
        ]
    }
)


def test_youtube_match_picks_the_stream_by_title(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    outputs = {
        "https://www.youtube.com/@LiveNOWFOX/streams": STREAMS,
        "https://www.youtube.com/watch?v=main": LIVE,
    }
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(make_settings(), runner=scripted(outputs, seen), fetcher=fetch)
    resolved = resolver.resolve(channel(youtube="https://www.youtube.com/@LiveNOWFOX/live", match="24/7"))
    assert resolved.height == 1080
    assert seen[1][-1] == "https://www.youtube.com/watch?v=main"


@pytest.mark.parametrize("listing", [json.dumps({"entries": []}), None])
def test_youtube_match_falls_back_to_the_live_page(
    make_settings: Callable[..., Settings], listing: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    seen: list[Sequence[str]] = []
    outputs = {"https://www.youtube.com/@LiveNOWFOX/live": LIVE}
    if listing is not None:
        outputs["https://www.youtube.com/@LiveNOWFOX/streams"] = listing
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(make_settings(), runner=scripted(outputs, seen), fetcher=fetch)
    resolver.resolve(channel(youtube="https://www.youtube.com/@LiveNOWFOX/live", match="24/7"))
    assert seen[-1][-1] == "https://www.youtube.com/@LiveNOWFOX/live"
    assert "using the live page" in caplog.text


TOKEN = "https://t.example.com/secure?url={url}"
SIGNED_REQUEST = "https://t.example.com/secure?url=https%3A%2F%2Fcdn.example.com%2Fatv%2Fatv.m3u8"


def test_hls_token_signs_the_url_before_each_tune(make_settings: Callable[..., Settings]) -> None:
    seen: list[tuple[str, Headers]] = []
    reply = json.dumps({"Success": True, "Url": "https://cdn.example.com/atv/atv.m3u8?st=abc&e=1"})
    table = {SIGNED_REQUEST: reply, "https://cdn.example.com/atv/atv.m3u8?st=abc&e=1": MASTER}

    def fetch(url: str, headers: Headers) -> tuple[str, str]:
        seen.append((url, headers))
        return url, table[url]

    source = channel(
        hls="https://cdn.example.com/atv/atv.m3u8", token=TOKEN, headers={"Referer": "https://tv.example/"}
    )
    resolved = Resolver(make_settings(), fetcher=fetch).resolve(source)
    assert resolved.video == "https://cdn.example.com/atv/1080.m3u8"
    assert resolved.headers == (("Referer", "https://tv.example/"),)
    assert seen[0] == (SIGNED_REQUEST, (("Referer", "https://tv.example/"),))


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (None, "token service failed"),
        ("<html>", "did not return JSON"),
        (json.dumps({"Success": False}), "no 'url' URL"),
        (json.dumps({"url": "javascript:alert(1)"}), "no 'url' URL"),
        (json.dumps(["https://x"]), "no 'url' URL"),
    ],
)
def test_hls_token_problems(make_settings: Callable[..., Settings], reply: str | None, message: str) -> None:
    fetch = pages({} if reply is None else {SIGNED_REQUEST: reply})
    source = channel(hls="https://cdn.example.com/atv/atv.m3u8", token=TOKEN)
    with pytest.raises(ResolveError, match=message):
        Resolver(make_settings(), fetcher=fetch).resolve(source)


def test_run_command_stops_when_cancelled() -> None:
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(LookupCancelledError):
        run_command([sys.executable, "-c", "import time; time.sleep(30)"], 60, cancel)
    assert time.monotonic() - started < 3


def test_cancelled_youtube_lookup_skips_the_fallback(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []

    def run(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        seen.append(args)
        raise LookupCancelledError

    resolver = Resolver(make_settings(), runner=run, fetcher=pages({}))
    with pytest.raises(LookupCancelledError):
        resolver.resolve(channel(youtube="https://www.youtube.com/@news/live", match="Live"))
    assert len(seen) == 1


def test_a_set_cancel_stops_before_the_playlist_request(make_settings: Callable[..., Settings]) -> None:
    cancel = threading.Event()
    cancel.set()
    fetch = pages({"https://cdn.example.com/live/master.m3u8": MASTER})
    with pytest.raises(LookupCancelledError):
        Resolver(make_settings(), fetcher=fetch).resolve(
            channel(hls="https://cdn.example.com/live/master.m3u8"), cancel
        )
