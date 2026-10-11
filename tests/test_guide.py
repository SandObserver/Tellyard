import gzip
import itertools
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tellyard import channels, guide
from tellyard.net import FetchError

NOW = datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="ATV.tr"><display-name>ATV</display-name></channel>
  <programme start="20261011150000 +0000" stop="20261011170000 +0000" channel="ATV.tr">
    <title lang="tr">Dizi</title><desc>Episode text</desc>
  </programme>
  <programme start="20261011170000 +0000" stop="20261011183000 +0000" channel="ATV.tr">
    <title lang="tr">Haber</title>
  </programme>
  <programme start="20261001000000 +0000" stop="20261001010000 +0000" channel="ATV.tr">
    <title>Old</title>
  </programme>
  <programme start="20261011150000 +0000" stop="20261011170000 +0000" channel="Other.tr">
    <title>Not ours</title>
  </programme>
  <programme start="bad" stop="20261011170000 +0000" channel="ATV.tr"><title>Broken</title></programme>
</tv>
"""


def lineup(**guide_entry: Any) -> channels.Lineup:
    entries = [
        {
            "number": 201,
            "name": "atv",
            "logo": "https://example.com/atv.png",
            "hls": "https://cdn.example.com/atv.m3u8",
            "guide": guide_entry,
        }
    ]
    return channels.parse({"channel": entries})


def serving(text: str, compress: bool = False) -> guide.Downloader:
    def download(url: str, destination: Path) -> None:
        data = text.encode()
        destination.write_bytes(gzip.compress(data) if compress else data)

    return download


def programmes(data: bytes) -> list[ET.Element]:
    return ET.fromstring(data).findall("programme")


def window(items: list[ET.Element]) -> tuple[str | None, str | None]:
    return items[0].get("start"), items[-1].get("stop")


@pytest.mark.parametrize("compress", [False, True])
def test_merges_real_listings_and_fills_gaps(compress: bool) -> None:
    data = guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, serving(SOURCE, compress))
    root = ET.fromstring(data)
    channel = root.find("channel")
    assert channel is not None
    assert channel.get("id") == "tellyard.201"
    assert [name.text for name in channel.findall("display-name")] == ["atv", "201"]
    items = programmes(data)
    titles = [item.findtext("title") for item in items]
    assert "Dizi" in titles and "Haber" in titles
    assert "Old" not in titles and "Not ours" not in titles and "Broken" not in titles
    assert all(item.get("channel") == "tellyard.201" for item in items)
    dizi = next(item for item in items if item.findtext("title") == "Dizi")
    assert dizi.findtext("desc") == "Episode text"
    assert window(items) == ("20261011133000 +0000", "20261012153000 +0000")
    for before, after in itertools.pairwise(items):
        assert before.get("stop") == after.get("start")


def test_placeholder_blocks_align_to_the_block_length() -> None:
    items = programmes(guide.build(lineup(hours=4, category="News"), NOW, 1))
    assert window(items) == ("20261011133000 +0000", "20261012153000 +0000")
    assert items[0].get("stop") == "20261011160000 +0000"
    assert items[1].get("stop") == "20261011200000 +0000"
    assert items[0].findtext("category") == "News"
    assert items[0].find("icon") is None


def test_failed_source_falls_back_to_placeholders(caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str, destination: Path) -> None:
        raise FetchError("epg.example.com: HTTP 503")

    items = programmes(guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, broken))
    assert {item.findtext("title") for item in items} == {"atv"}
    assert "using placeholders" in caplog.text


def test_reports_ids_missing_from_the_source(caplog: pytest.LogCaptureFixture) -> None:
    guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="Nope.tr"), NOW, 1, serving(SOURCE))
    assert "no current listings for Nope.tr" in caplog.text


def test_unparseable_source_falls_back() -> None:
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving("<tv><")))
    assert {item.findtext("title") for item in items} == {"atv"}


def test_parse_time_variants() -> None:
    assert guide.parse_time("20261011150000 +0300") == datetime(2026, 10, 11, 12, tzinfo=UTC)
    assert guide.parse_time("20261011150000") == datetime(2026, 10, 11, 15, tzinfo=UTC)
    assert guide.parse_time("2026") is None
    assert guide.parse_time("20261011150000 nonsense") is None


def test_guide_cache_survives_restarts(tmp_path: Path) -> None:
    path = tmp_path / "data" / "guide.xml"
    cache = guide.Guide(path, 1)
    assert cache.data is None
    cache.rebuild(lineup(), NOW)
    assert path.read_bytes() == cache.data
    assert cache.built_at == NOW
    reopened = guide.Guide(path, 1)
    assert reopened.data == cache.data
    assert reopened.built_at is not None
    assert datetime.now(UTC) - reopened.built_at < timedelta(minutes=1)


def test_entries_are_decorated_for_plex() -> None:
    source = SOURCE.replace("<title>Old</title>", "<title>Old</title>").replace(
        '<title lang="tr">Haber</title>', '<title lang="tr">Haber</title><category>Film</category>'
    )
    data = guide.build(
        lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr", category="Drama"), NOW, 1, serving(source)
    )
    items = programmes(data)
    dizi = next(item for item in items if item.findtext("title") == "Dizi")
    haber = next(item for item in items if item.findtext("title") == "Haber")
    filler = next(item for item in items if item.findtext("title") == "atv")
    assert dizi.findtext("category") == "Drama"
    assert dizi.findtext("episode-num") == "2026-10-11 15:00:00"
    assert [child.tag for child in dizi] == ["title", "desc", "category", "episode-num"]
    assert haber.find("episode-num") is None
    assert all(item.find("icon") is None for item in (dizi, haber, filler))
    channel_icon = ET.fromstring(data).find("channel/icon")
    assert channel_icon is not None and channel_icon.get("src") == "https://example.com/atv.png"


def test_existing_icons_are_kept() -> None:
    source = SOURCE.replace(
        "<desc>Episode text</desc>", '<desc>Episode text</desc><icon src="https://own.example/x.jpg"/>'
    )
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving(source)))
    dizi = next(item for item in items if item.findtext("title") == "Dizi")
    assert [icon.get("src") for icon in dizi.findall("icon")] == ["https://own.example/x.jpg"]
