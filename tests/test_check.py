import dataclasses
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from tellyard import channels, check
from tellyard.relay import Relay
from tellyard.resolve import Resolved, ResolveError
from tellyard.settings import Settings
from tests.fakes import FakeResolver

FFMPEG_OUTPUT = """Input #0, hls, from 'http://127.0.0.1:1/x/0/index.m3u8':
  Duration: N/A, start: 1.400000, bitrate: N/A
  Program 0
  Stream #0:0: Video: h264 (High) ([27][0][0][0] / 0x001B), yuv420p(tv, bt709, progressive), 1280x720, 30 fps
  Stream #0:1(eng): Audio: aac (LC) ([15][0][0][0] / 0x000F), 48000 Hz, stereo, fltp
  Stream #0:2: Video: h264 (High), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 29.97 fps, 90k tbn
At least one output file must be specified
"""


def test_probe_height_reads_ffmpeg_stream_details(relay: Relay) -> None:
    seen: list[Sequence[str]] = []

    def runner(args: Sequence[str], timeout: float, cancel: threading.Event | None) -> str:
        seen.append(args)
        return FFMPEG_OUTPUT

    resolved = Resolved("https://v.example.com/a.m3u8", None, 1080, (("Referer", "https://r.example.com/"),))
    assert check.probe_height("ffmpeg", relay, resolved, runner) == 1080
    assert seen[0][-1].startswith(f"{relay.base}/")
    assert "-headers" not in seen[0]
    assert check.probe_height("ffmpeg", relay, resolved, lambda args, timeout, cancel: "") is None
    audio_only = "  Stream #0:0: Audio: aac, 48000 Hz, stereo\nAt least one output file must be specified\n"
    assert check.probe_height("ffmpeg", relay, resolved, lambda args, timeout, cancel: audio_only) is None


def test_probe_height_reports_open_errors(relay: Relay) -> None:
    output = "Error opening input file https://user:pw@v.example.com/a.m3u8.\nError opening input files: HTTP 403\n"
    resolved = Resolved("https://v.example.com/a.m3u8", None, 1080, ())
    with pytest.raises(ResolveError, match=r"^Error opening input files: HTTP 403$"):
        check.probe_height("ffmpeg", relay, resolved, lambda args, timeout, cancel: output)


def test_run_probe_returns_stderr(tmp_path: Path) -> None:
    script = tmp_path / "probe"
    script.write_text("#!/bin/sh\necho details >&2\nexit 1\n")
    script.chmod(0o755)
    assert check.run_probe([str(script)], 5.0) == "details\n"
    with pytest.raises(ResolveError, match="is not installed"):
        check.run_probe([str(tmp_path / "missing")], 5.0)
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    with pytest.raises(ResolveError, match="took longer than 0 s"):
        check.run_probe([str(slow)], 0.2)


def test_check_and_report(make_settings: Callable[..., Settings], channels_file: Path) -> None:
    lineup = channels.load(channels_file)
    heights = iter([1080, 720])
    results = check.check(lineup.channels, FakeResolver(make_settings()), lambda resolved: next(heights), 1080)
    assert [result.ok for result in results] == [True, False]
    text = check.report(results)
    assert "1080p  ok" in text
    assert "720p  FAIL  below the 1080p minimum" in text
    assert text.endswith("1 of 2 channels passed")


def test_minimum_is_off_unless_set(make_settings: Callable[..., Settings], channels_file: Path) -> None:
    lineup = channels.load(channels_file)
    resolver = FakeResolver(make_settings())
    assert all(result.ok for result in check.check(lineup.channels, resolver, lambda resolved: 480))
    override = dataclasses.replace(lineup.channels[0], min_height=720)
    assert check.check([override], resolver, lambda resolved: 720, 1080)[0].ok


def test_check_failures(make_settings: Callable[..., Settings], channels_file: Path) -> None:
    lineup = channels.load(channels_file)
    results = check.check(lineup.channels, FakeResolver(make_settings(), failures=1), lambda resolved: None)
    assert results[0].problem == "the YouTube channel is not live right now"
    assert results[1].problem == "no video stream found"

    def raising(resolved: Resolved) -> int | None:
        raise ResolveError("ffmpeg took longer than 45 s")

    assert check.check(lineup.channels[:1], FakeResolver(make_settings()), raising)[0].height is None
