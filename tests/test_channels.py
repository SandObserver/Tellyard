from pathlib import Path
from typing import Any

import pytest

from tellyard import channels
from tellyard.channels import ChannelError, HlsSource, LiveSource, PageSource

LOGO = "https://example.com/logo.png"


def entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"number": 101, "name": "News", "logo": LOGO, "live": "https://www.youtube.com/@n/live"}
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def parse(*entries: dict[str, Any], **top: Any) -> channels.Lineup:
    return channels.parse({"channel": list(entries), **top})


def test_loads_the_example_file() -> None:
    lineup = channels.load(Path(__file__).parent.parent / "channels.example.toml")
    assert {type(channel.source).__name__ for channel in lineup.channels} == {
        "LiveSource",
        "HlsSource",
        "PageSource",
    }


def test_parses_each_source_kind() -> None:
    lineup = parse(
        entry(number=1),
        entry(number=2, live=None, hls="https://cdn.example.com/a.m3u8", headers={"Referer": "https://x.example/"}),
        entry(number=3, live=None, page="https://example.com/live", match=" cdn "),
    )
    live, hls, page = (channel.source for channel in lineup.channels)
    assert isinstance(live, LiveSource)
    assert hls == HlsSource("https://cdn.example.com/a.m3u8", (("Referer", "https://x.example/"),))
    assert page == PageSource("https://example.com/live", "cdn", ())


def test_live_match_keeps_the_title_text() -> None:
    channel = parse(entry(live="https://www.youtube.com/@LiveNOWFOX/live", match=" 24/7 ")).channels[0]
    assert channel.source == LiveSource("https://www.youtube.com/@LiveNOWFOX/live", "24/7")


def test_live_accepts_other_sites_and_the_youtube_key() -> None:
    lineup = parse(
        entry(number=1, live="https://www.twitch.tv/example"),
        entry(number=2, live=None, youtube="https://www.youtube.com/@n/live"),
    )
    assert [channel.source for channel in lineup.channels] == [
        LiveSource("https://www.twitch.tv/example"),
        LiveSource("https://www.youtube.com/@n/live"),
    ]


def test_hls_token_settings() -> None:
    channel = parse(
        entry(live=None, hls="https://cdn.example.com/a.m3u8", token="https://t.example.com/s?url={url}")
    ).channels[0]
    assert channel.source == HlsSource("https://cdn.example.com/a.m3u8", (), "https://t.example.com/s?url={url}", "url")


def test_sorts_by_number_including_subchannels() -> None:
    lineup = parse(entry(number="10.2"), entry(number=9), entry(number="10.1"))
    assert [channel.number for channel in lineup.channels] == ["9", "10.1", "10.2"]
    assert lineup.find("10.1") is not None
    assert lineup.find("11") is None


def test_placeholder_defaults_come_from_the_name() -> None:
    channel = parse(entry()).channels[0]
    assert channel.placeholder.title == "News"
    assert channel.placeholder.description == "Live on News."
    assert channel.placeholder.block_minutes == 120
    assert channel.xmltv is None
    assert channel.guide_id == "tellyard.101"


def test_xmltv_guide_uses_named_source() -> None:
    lineup = parse(
        entry(guide={"xmltv": "turkey", "id": "ATV.tr", "hours": 3}),
        guide_sources={"turkey": "https://epg.example.com/tr.xml.gz"},
    )
    channel = lineup.channels[0]
    assert channel.xmltv == channels.XmltvGuide("https://epg.example.com/tr.xml.gz", "ATV.tr")
    assert channel.placeholder.block_minutes == 180


def test_xmltv_guide_accepts_a_url() -> None:
    channel = parse(entry(guide={"xmltv": "https://epg.example.com/a.xml", "id": "A"})).channels[0]
    assert channel.xmltv is not None
    assert channel.xmltv.url == "https://epg.example.com/a.xml"


def test_min_height_is_optional_per_channel() -> None:
    lineup = parse(entry(number=1), entry(number=2, min_height=720))
    assert [channel.min_height for channel in lineup.channels] == [None, 720]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"channel": []}, "at least one"),
        ({"channels": []}, "unknown top-level"),
        ({"channel": [entry()], "defaults": {"min_height": 1080}}, "MIN_HEIGHT"),
        ({"channel": [entry(), entry()]}, "more than once"),
        ({"channel": [entry(number="1a")]}, "number must look like"),
        ({"channel": [entry(number=True)]}, "number must look like"),
        ({"channel": [entry(number=101.1)]}, 'in quotes, such as number = "101.1"'),
        ({"channel": [entry(name=" ")]}, "name must be non-empty"),
        ({"channel": [entry(logo="ftp://x")]}, "logo must be an http"),
        ({"channel": [entry(live=None)]}, "exactly one of"),
        ({"channel": [entry(hls="https://a.example/x.m3u8")]}, "exactly one of"),
        ({"channel": [entry(youtube="https://www.youtube.com/@n/live")]}, "exactly one of hls, live or page"),
        ({"channel": [entry(live="https://www.twitch.tv/example", match="x")]}, "needs a YouTube channel URL"),
        ({"channel": [entry(colour="red")]}, "unknown keys colour"),
        ({"channel": [entry(live=None, hls="https://a.example/x.m3u8", match="x")]}, "match only applies"),
        ({"channel": [entry(live="https://www.youtube.com/watch?v=abc", match="x")]}, "needs a YouTube channel URL"),
        ({"channel": [entry(headers={"Referer": "https://x/"})]}, "not supported for live"),
        ({"channel": [entry(live=None, hls="https://a.example/x", headers={"Bad Name": "x"})]}, "not valid"),
        ({"channel": [entry(live=None, hls="https://a.example/x", headers={"X": "a\r\nb"})]}, "not valid"),
        ({"channel": [entry(live=None, page="https://a.example/", match=3)]}, "match must be text"),
        ({"channel": [entry(guide={"hours": 5})]}, "divide a day"),
        ({"channel": [entry(guide={"hours": 30})]}, "between 0.5 and 24"),
        ({"channel": [entry(guide={"id": "x"})]}, "id needs xmltv"),
        ({"channel": [entry(guide={"xmltv": "nowhere", "id": "x"})]}, "a URL or a [guide_sources] name"),
        ({"channel": [entry(guide={"xmltv": "https://a.example/g.xml"})]}, "guide id must be non-empty"),
        ({"channel": [entry(guide={"category": 1})]}, "category must be text"),
        ({"channel": [entry(guide={"tags": []})]}, "unknown keys tags"),
        ({"channel": [entry(min_height=0)]}, "positive whole number"),
        ({"channel": [entry(token="https://t.example/s?url={url}")]}, "token only applies to hls"),
        ({"channel": [entry(live=None, hls="https://a.example/x", token_field="u")]}, "token_field needs token"),
        ({"channel": [entry(live=None, hls="https://a.example/x", token="nope")]}, "token must be an http"),
        ({"channel": [entry()], "guide_sources": {"Bad Name": "https://x/"}}, "lowercase"),
        ({"channel": ["not a table"]}, "must be a table"),
    ],
)
def test_rejects_mistakes(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ChannelError, match=message.replace("[", r"\[").replace("]", r"\]")):
        channels.parse(data)


def test_error_names_the_channel() -> None:
    with pytest.raises(ChannelError, match="channel 'Drama'"):
        parse(entry(name="Drama", logo="nope"))


def test_load_reports_unreadable_and_invalid_files(tmp_path: Path) -> None:
    with pytest.raises(ChannelError, match="cannot read"):
        channels.load(tmp_path / "missing.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("[[channel]\n", encoding="utf-8")
    with pytest.raises(ChannelError, match="not valid TOML"):
        channels.load(broken)


def test_load_reports_a_file_that_is_not_utf8(tmp_path: Path) -> None:
    latin = tmp_path / "latin.toml"
    latin.write_bytes('[[channel]]\nname = "T\xe9l\xe9"\n'.encode("latin-1"))
    with pytest.raises(ChannelError, match="not UTF-8 text"):
        channels.load(latin)
