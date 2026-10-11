import dataclasses
import json
from collections.abc import Callable, Sequence
from pathlib import Path

from tellyard import channels, check
from tellyard.resolve import Resolved, ResolveError
from tellyard.settings import Settings
from tests.fakes import FakeResolver


def test_probe_height_reads_ffprobe_json() -> None:
    seen: list[Sequence[str]] = []

    def runner(args: Sequence[str], timeout: float) -> str:
        seen.append(args)
        return json.dumps({"streams": [{"height": 720}, {"height": 1080}, {"codec": "x"}]})

    resolved = Resolved("https://v.example.com/a.m3u8", None, 1080, (("Referer", "https://r.example.com/"),))
    assert check.probe_height("ffprobe", resolved, runner) == 1080
    assert seen[0][-1] == "https://v.example.com/a.m3u8"
    assert "-headers" in seen[0]
    assert check.probe_height("ffprobe", resolved, lambda args, timeout: "") is None


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
        raise ResolveError("ffprobe took longer than 45 s")

    assert check.check(lineup.channels[:1], FakeResolver(make_settings()), raising)[0].height is None
