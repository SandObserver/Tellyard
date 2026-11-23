import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

import pytest

from tellyard import channels, resolve
from tellyard.channels import Channel
from tellyard.net import Cancel, FetchError, Headers, TooLargeError, Trust, trust_of
from tellyard.resolve import Fetcher, LookupCancelledError, Resolved, ResolveError, Resolver, Runner, run_command
from tellyard.settings import Settings

MASTER = (
    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720\n720.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1920x1080\n1080.m3u8\n"
)


def channel(**source: object) -> Channel:
    entry = {"number": 1, "name": "Test", "logo": "https://example.com/l.png", **source}
    return channels.parse({"channel": [entry]}).channels[0]


def pages(mapping: dict[str, str]) -> Fetcher:
    def fetch(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        if url not in mapping:
            raise FetchError(f"{url}: HTTP 404")
        return url, mapping[url]

    return fetch


PROXY = "socks5h://127.0.0.1:9"


@contextlib.contextmanager
def fixed_proxy(trust: Trust, fields: Mapping[str, str]) -> Iterator[str]:
    yield PROXY


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
    assert resolved == Resolved(
        "https://cdn.example.com/live/1080.m3u8", None, 1080, (), trust_of(["https://cdn.example.com/live/master.m3u8"])
    )


def test_media_playlist_is_used_as_is(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://cdn.example.com/a.m3u8": "#EXTM3U\n#EXTINF:6,\nseg.ts\n"})
    resolved = Resolver(make_settings(), fetcher=fetch).resolve(channel(hls="https://cdn.example.com/a.m3u8"))
    assert resolved.video == "https://cdn.example.com/a.m3u8"
    assert resolved.height is None


def test_rejects_a_non_playlist(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://cdn.example.com/a.m3u8": "<html>blocked</html>"})
    with pytest.raises(ResolveError, match="did not return an HLS playlist"):
        Resolver(make_settings(), fetcher=fetch).resolve(channel(hls="https://cdn.example.com/a.m3u8"))


def test_retries_a_hanging_playlist_request(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    attempts: list[str] = []

    def flaky(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        attempts.append(url)
        if len(attempts) < 3:
            raise FetchError("cdn.example.com: The read operation timed out")
        return url, MASTER

    resolved = Resolver(make_settings(), fetcher=flaky).resolve(channel(hls="https://cdn.example.com/m.m3u8"))
    assert resolved.height == 1080
    assert len(attempts) == 3
    retries = [r for r in caplog.records if r.getMessage() == "playlist request failed, retrying"]
    assert [(r.channel, r.title) for r in retries] == [("1", "Test")] * 2  # type: ignore[attr-defined]


def test_gives_up_after_three_playlist_attempts(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ResolveError, match="could not load the playlist"):
        Resolver(make_settings(), fetcher=pages({})).resolve(channel(hls="https://cdn.example.com/m.m3u8"))


@pytest.mark.parametrize(
    ("error", "tries"),
    [
        (FetchError("cdn.example.com: HTTP 404", 404), 1),
        (TooLargeError("cdn.example.com: response is larger than 4 MB"), 1),
        (FetchError("cdn.example.com: HTTP 429", 429), 3),
        (FetchError("cdn.example.com: HTTP 503", 503), 3),
    ],
)
def test_retries_only_playlist_errors_that_can_pass(
    make_settings: Callable[..., Settings], error: FetchError, tries: int
) -> None:
    attempts: list[str] = []

    def failing(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        attempts.append(url)
        raise error

    with pytest.raises(ResolveError, match="could not load the playlist"):
        Resolver(make_settings(), fetcher=failing).resolve(channel(hls="https://cdn.example.com/m.m3u8"))
    assert len(attempts) == tries


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
    def broken(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        raise ValueError("bad value")

    with pytest.raises(ResolveError, match="failed unexpectedly"):
        Resolver(make_settings(), fetcher=broken).resolve(channel(hls="https://cdn.example.com/m.m3u8"))
    assert "stream lookup failed unexpectedly" in caplog.text


def test_youtube_uses_the_live_manifest(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    info = json.dumps({"live_status": "is_live", "manifest_url": "https://manifest.googlevideo.com/master.m3u8"})
    fetch = pages({"https://manifest.googlevideo.com/master.m3u8": MASTER})
    settings = make_settings(YTDLP="/opt/yt-dlp", YTDLP_ARGS="--extractor-args youtube:x")
    resolver = Resolver(settings, runner=runner_returning(info, seen), fetcher=fetch, proxy=fixed_proxy)
    resolved = resolver.resolve(channel(live="https://www.youtube.com/@news/live"))
    assert resolved.height == 1080
    assert seen[0][0] == "/opt/yt-dlp"
    assert seen[0][-5:] == ["--proxy", PROXY, "--extractor-args", "youtube:x", "https://www.youtube.com/@news/live"]
    assert seen[0][1:6] == ["--ignore-config", "--no-plugin-dirs", "--no-warnings", "--xff", "never"]


def test_youtube_falls_back_to_format_manifests(make_settings: Callable[..., Settings]) -> None:
    info = json.dumps(
        {"live_status": "is_live", "formats": [{"format_id": "sb"}, {"manifest_url": "https://m.example.com/x.m3u8"}]}
    )
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(make_settings(), runner=runner_returning(info), fetcher=fetch)
    assert resolver.resolve(channel(youtube="https://www.youtube.com/@n/live")).height == 1080


@pytest.mark.parametrize(
    ("url", "repair"),
    [
        ("https://www.twitch.tv/example", True),
        ("https://twitch.tv/example", True),
        ("https://www.youtube.com/@n/live", False),
        ("https://nottwitch.tv/example", False),
    ],
)
def test_live_repairs_timestamps_only_for_twitch(
    make_settings: Callable[..., Settings], url: str, repair: bool
) -> None:
    info = json.dumps({"live_status": "is_live", "manifest_url": "https://m.example.com/x.m3u8"})
    resolver = Resolver(
        make_settings(), runner=runner_returning(info), fetcher=pages({"https://m.example.com/x.m3u8": MASTER})
    )
    assert resolver.resolve(channel(live=url)).repair_timestamps is repair


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
    geo = "ERROR: [x] 1: not available in your country\nAvailable in DE.\nYou might want to use a VPN.\n"
    with pytest.raises(ResolveError, match=r"not available in your country$"):
        run_command([sys.executable, "-c", f"import sys; sys.stderr.write({geo!r}); sys.exit(1)"], 10)
    with pytest.raises(ResolveError, match="exited with code 4"):
        run_command([sys.executable, "-c", "import sys; sys.exit(4)"], 10)
    with pytest.raises(ResolveError, match="took longer"):
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.2)
    with pytest.raises(ResolveError, match="is not installed"):
        run_command(["/nonexistent/yt-dlp"], 10)
    with pytest.raises(ResolveError, match="did not start"):
        run_command([str(Path(sys.executable).parent)], 10)


def test_run_command_reports_a_missing_interpreter(tmp_path: Path) -> None:
    script = tmp_path / "yt-dlp"
    script.write_text("#!/nonexistent/python3\n")
    script.chmod(0o755)
    with pytest.raises(ResolveError, match="did not start: its interpreter or loader is missing"):
        run_command([str(script)], 10)


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
    assert all(args[1:6] == ["--ignore-config", "--no-plugin-dirs", "--no-warnings", "--xff", "never"] for args in seen)


@pytest.mark.parametrize("listing", [json.dumps({"entries": []}), None])
@pytest.mark.parametrize("url", ["https://www.youtube.com/@LiveNOWFOX/live", "https://www.youtube.com/@LiveNOWFOX/"])
def test_youtube_match_falls_back_to_the_live_page(
    make_settings: Callable[..., Settings], listing: str | None, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    seen: list[Sequence[str]] = []
    outputs = {"https://www.youtube.com/@LiveNOWFOX/live": LIVE}
    if listing is not None:
        outputs["https://www.youtube.com/@LiveNOWFOX/streams"] = listing
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(make_settings(), runner=scripted(outputs, seen), fetcher=fetch)
    resolver.resolve(channel(youtube=url, match="24/7"))
    assert seen[-1][-1] == "https://www.youtube.com/@LiveNOWFOX/live"
    fallback = [r for r in caplog.records if "using the live page" in r.getMessage()]
    assert [(r.channel, r.title) for r in fallback] == [("1", "Test")]  # type: ignore[attr-defined]


TOKEN = "https://t.example.com/secure?url={url}"
SIGNED_REQUEST = "https://t.example.com/secure?url=https%3A%2F%2Fcdn.example.com%2Fatv%2Fatv.m3u8"


def test_hls_token_signs_the_url_before_each_tune(make_settings: Callable[..., Settings]) -> None:
    seen: list[tuple[str, Headers]] = []
    reply = json.dumps({"Success": True, "Url": "https://cdn.example.com/atv/atv.m3u8?st=abc&e=1"})
    table = {SIGNED_REQUEST: reply, "https://cdn.example.com/atv/atv.m3u8?st=abc&e=1": MASTER}

    def fetch(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
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


def helper_program(pid_file: Path) -> list[str]:
    helper = (
        "import os, subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(pid_file)!r} + '.tmp', 'w').write(str(p.pid))\n"
        f"os.replace({str(pid_file)!r} + '.tmp', {str(pid_file)!r})\n"
        "time.sleep(30)\n"
    )
    return [sys.executable, "-c", helper]


def assert_stopped(pid_file: Path) -> None:
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("the helper process kept running")


def test_run_command_stops_the_programs_it_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "helper.pid"
    monkeypatch.setattr(resolve, "monotonic", lambda: time.monotonic() + (100 if pid_file.exists() else 0))
    with pytest.raises(ResolveError, match="took longer"):
        run_command(helper_program(pid_file), 30)
    assert_stopped(pid_file)


class Interrupted(threading.Event):
    def __init__(self, pid_file: Path) -> None:
        super().__init__()
        self.pid_file = pid_file

    def is_set(self) -> bool:
        if self.pid_file.exists():
            raise KeyboardInterrupt
        return False


def test_run_command_stops_the_programs_it_started_on_ctrl_c(tmp_path: Path) -> None:
    pid_file = tmp_path / "helper.pid"
    with pytest.raises(KeyboardInterrupt):
        run_command(helper_program(pid_file), 30, Interrupted(pid_file))
    assert_stopped(pid_file)


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
    cancel = Cancel()
    cancel.set()
    fetch = pages({"https://cdn.example.com/live/master.m3u8": MASTER})
    with pytest.raises(LookupCancelledError):
        Resolver(make_settings(), fetcher=fetch).resolve(
            channel(hls="https://cdn.example.com/live/master.m3u8"), cancel
        )


def test_run_command_stops_output_that_is_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve, "OUTPUT_LIMIT", 1000)
    pid_file = tmp_path / "helper.pid"
    helper = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
        "sys.stdout.write('x' * 5000)\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    started = time.monotonic()
    with pytest.raises(ResolveError, match="returned more than"):
        run_command([sys.executable, "-c", helper], 30)
    assert time.monotonic() - started < 5
    assert_stopped(pid_file)


def test_run_command_keeps_the_last_error_of_long_output() -> None:
    script = "import sys; sys.stderr.write('noise\\n' * 50000 + 'ERROR: the real reason\\n'); sys.exit(1)"
    with pytest.raises(ResolveError, match=r"^ERROR: the real reason$"):
        run_command([sys.executable, "-c", script], 10)


def test_leaving_during_a_playlist_request_stops_it(make_settings: Callable[..., Settings]) -> None:
    listener = socket.create_server(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/live/master.m3u8"
    cancel = Cancel()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with listener, pytest.raises(LookupCancelledError):
        Resolver(make_settings()).resolve(channel(hls=url), cancel)
    assert time.monotonic() - started < 2


def test_lookup_caches_follow_the_data_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("DENO_DIR", raising=False)
    env = resolve.lookup_env(tmp_path)
    assert env["XDG_CACHE_HOME"] == str(tmp_path / "cache")
    assert env["DENO_DIR"] == str(tmp_path / "cache" / "deno")
    monkeypatch.setenv("DENO_DIR", "/elsewhere")
    assert resolve.lookup_env(tmp_path)["DENO_DIR"] == "/elsewhere"


def test_run_command_passes_the_environment() -> None:
    script = "import os; print(os.environ['DENO_DIR'])"
    output = run_command([sys.executable, "-c", script], 10.0, None, {"DENO_DIR": "/data/cache/deno"})
    assert output.strip() == "/data/cache/deno"


def counting_runner(output: str, seen: list[Sequence[str]]) -> Runner:
    def run(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        seen.append(args)
        return output

    return run


def live_info(manifest: str) -> str:
    return json.dumps({"live_status": "is_live", "manifest_url": manifest})


def test_live_address_is_remembered_between_tunes(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(
        make_settings(), runner=counting_runner(live_info("https://m.example.com/x.m3u8"), seen), fetcher=fetch
    )
    live = channel(live="https://www.youtube.com/@news/live")
    first, second = resolver.resolve(live), resolver.resolve(live)
    assert len(seen) == 1
    assert (first.remembered, second.remembered) == (False, True)
    assert second.height == 1080


def test_fresh_lookup_skips_the_remembered_address(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(
        make_settings(), runner=counting_runner(live_info("https://m.example.com/x.m3u8"), seen), fetcher=fetch
    )
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    assert resolver.resolve(live, fresh=True).remembered is False
    assert len(seen) == 2


def test_failed_remembered_address_is_looked_up_again(
    make_settings: Callable[..., Settings], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    manifests = iter(["https://m.example.com/old.m3u8", "https://m.example.com/new.m3u8"])
    live_pages = {"https://m.example.com/old.m3u8": MASTER, "https://m.example.com/new.m3u8": MASTER}

    def run(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        return live_info(next(manifests))

    resolver = Resolver(make_settings(), runner=run, fetcher=pages(live_pages))
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    del live_pages["https://m.example.com/old.m3u8"]
    resolved = resolver.resolve(live)
    assert resolved.remembered is False
    assert resolved.video == "https://m.example.com/1080.m3u8"
    assert "remembered stream address failed, looking it up again" in caplog.text
    assert resolver.resolve(live).remembered is True


def test_remembered_address_expires(make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Sequence[str]] = []
    clock = [1000.0]
    monkeypatch.setattr(resolve, "monotonic", lambda: clock[0])
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(
        make_settings(), runner=counting_runner(live_info("https://m.example.com/x.m3u8"), seen), fetcher=fetch
    )
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    clock[0] += resolve.REMEMBER_SECONDS - 1
    assert resolver.resolve(live).remembered is True
    clock[0] += 2
    assert resolver.resolve(live).remembered is False
    assert len(seen) == 2


@pytest.mark.parametrize(
    ("template", "seconds", "remembered"),
    [
        ("https://m.example.com/api/expire/{at}/ip/1/x.m3u8", 200, False),
        ("https://m.example.com/x.m3u8?Expires={at}&sig=a", 200, False),
        ("https://m.example.com/x.m3u8?hdnts=st%3D1~exp%3D{at}~acl%3D*", 200, False),
        ("https://m.example.com/x.m3u8?hdnts=exp%3D{at}~acl%3D%2F*~hmac%3Dab", 200, False),
        ("https://m.example.com/x.m3u8?hdnea=exp={at}~acl=/*~hmac=ab", 200, False),
        ("https://m.example.com/api/expire/{at}/x.m3u8", 6 * 3600, True),
    ],
)
def test_address_close_to_its_expiry_is_not_remembered(
    make_settings: Callable[..., Settings], template: str, seconds: int, remembered: bool
) -> None:
    manifest = template.format(at=int(time.time()) + seconds)
    fetch = pages({manifest: MASTER})
    resolver = Resolver(make_settings(), runner=runner_returning(live_info(manifest)), fetcher=fetch)
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    assert resolver.resolve(live).remembered is remembered


def test_page_address_is_remembered(make_settings: Callable[..., Settings]) -> None:
    loads: list[str] = []
    found = pages(
        {
            "https://tv.example.com/live": 'src="https://cdn.example.com/m.m3u8"',
            "https://cdn.example.com/m.m3u8": MASTER,
        }
    )

    def fetch(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        loads.append(url)
        return found(url, headers, trust, cancel=cancel)

    resolver = Resolver(make_settings(), fetcher=fetch)
    page = channel(page="https://tv.example.com/live")
    resolver.resolve(page)
    assert resolver.resolve(page).remembered is True
    assert loads.count("https://tv.example.com/live") == 1


@pytest.mark.parametrize(
    "source",
    [
        {"hls": "https://cdn.example.com/m.m3u8"},
        {"hls": "https://cdn.example.com/m.m3u8", "token": "https://sign.example.com/?u={url}"},
    ],
)
def test_hls_addresses_are_never_remembered(make_settings: Callable[..., Settings], source: dict[str, str]) -> None:
    loads: list[str] = []
    signed = json.dumps({"url": "https://cdn.example.com/m.m3u8"})
    found = pages(
        {
            "https://cdn.example.com/m.m3u8": MASTER,
            "https://sign.example.com/?u=https%3A%2F%2Fcdn.example.com%2Fm.m3u8": signed,
        }
    )

    def fetch(url: str, headers: Headers, trust: Trust, *, cancel: Cancel | None = None) -> tuple[str, str]:
        loads.append(url)
        return found(url, headers, trust, cancel=cancel)

    resolver = Resolver(make_settings(), fetcher=fetch)
    hls_channel = channel(**source)
    resolver.resolve(hls_channel)
    assert resolver.resolve(hls_channel).remembered is False
    assert len(loads) == 2 * (2 if "token" in source else 1)


def test_cancel_during_a_remembered_lookup_is_not_retried(make_settings: Callable[..., Settings]) -> None:
    seen: list[Sequence[str]] = []
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(
        make_settings(), runner=counting_runner(live_info("https://m.example.com/x.m3u8"), seen), fetcher=fetch
    )
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    cancel = Cancel()
    cancel.set()
    with pytest.raises(LookupCancelledError):
        resolver.resolve(live, cancel)
    assert len(seen) == 1


def test_forget_drops_the_remembered_address(make_settings: Callable[..., Settings]) -> None:
    fetch = pages({"https://m.example.com/x.m3u8": MASTER})
    resolver = Resolver(
        make_settings(), runner=runner_returning(live_info("https://m.example.com/x.m3u8")), fetcher=fetch
    )
    live = channel(live="https://www.youtube.com/@news/live")
    resolver.resolve(live)
    resolver.forget(live)
    assert resolver.resolve(live).remembered is False
