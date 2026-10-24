import gzip
import itertools
import os
import tracemalloc
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tellyard import channels, guide
from tellyard.net import FetchError, TooLargeError

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
    data = guide.build(
        lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, serving(SOURCE, compress)
    ).data
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


def test_overlapping_shows_end_when_the_next_starts() -> None:
    source = """<tv>
  <programme start="20261011150000 +0000" stop="20261011170000 +0000" channel="ATV.tr"><title>First</title></programme>
  <programme start="20261011160000 +0000" stop="20261011180000 +0000" channel="ATV.tr"><title>Second</title></programme>
  <programme start="20261011180000 +0000" stop="20261011200000 +0000" channel="ATV.tr"><title>Long</title></programme>
  <programme start="20261011183000 +0000" stop="20261011190000 +0000" channel="ATV.tr"><title>Inside</title></programme>
</tv>"""
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving(source)).data)
    times = {item.findtext("title"): (item.get("start"), item.get("stop")) for item in items}
    assert times["First"] == ("20261011150000 +0000", "20261011160000 +0000")
    assert times["Second"] == ("20261011160000 +0000", "20261011180000 +0000")
    assert times["Long"] == ("20261011180000 +0000", "20261011183000 +0000")
    assert times["Inside"] == ("20261011183000 +0000", "20261011190000 +0000")
    for before, after in itertools.pairwise(items):
        assert before.get("stop") == after.get("start")


def test_placeholder_blocks_align_to_the_block_length() -> None:
    items = programmes(guide.build(lineup(hours=4, category="News"), NOW, 1).data)
    assert window(items) == ("20261011133000 +0000", "20261012153000 +0000")
    assert items[0].get("stop") == "20261011160000 +0000"
    assert items[1].get("stop") == "20261011200000 +0000"
    assert items[0].findtext("category") == "News"
    assert items[0].find("icon") is None


def test_failed_source_falls_back_to_placeholders(caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str, destination: Path) -> None:
        raise FetchError("epg.example.com: HTTP 503")

    items = programmes(guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, broken).data)
    assert {item.findtext("title") for item in items} == {"atv"}
    assert "using placeholders" in caplog.text


def test_failed_source_uses_the_last_good_copy(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str, destination: Path) -> None:
        raise FetchError("epg.example.com: HTTP 503")

    saved = tmp_path / "feeds"
    channel = lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr")
    fresh = guide.build(channel, NOW, 1, serving(SOURCE), saved)
    assert fresh.failed == 0
    later = guide.build(channel, NOW, 1, broken, saved)
    assert later.failed == 1
    assert later.data == fresh.data
    assert "using the last good copy" in caplog.text
    assert "using placeholders" not in caplog.text


def test_unreadable_copy_falls_back_to_placeholders(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    saved = tmp_path / "feeds"
    channel = lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr")
    guide.build(channel, NOW, 1, serving(SOURCE), saved)
    for kept in saved.iterdir():
        kept.write_text("<tv><")
    items = programmes(guide.build(channel, NOW, 1, serving("<tv><"), saved).data)
    assert {item.findtext("title") for item in items} == {"atv"}
    assert "using placeholders" in caplog.text


def test_copies_of_removed_sources_are_deleted(tmp_path: Path) -> None:
    saved = tmp_path / "feeds"
    guide.build(lineup(xmltv="https://a.example.com/x", id="ATV.tr"), NOW, 1, serving(SOURCE), saved)
    first = {kept.name for kept in saved.iterdir()}
    guide.build(lineup(xmltv="https://b.example.com/x", id="ATV.tr"), NOW, 1, serving(SOURCE), saved)
    second = {kept.name for kept in saved.iterdir()}
    assert len(first) == len(second) == 1
    assert first != second


def test_copy_not_saved_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    saved = tmp_path / "feeds"
    saved.write_text("not a folder")
    data = guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, serving(SOURCE), saved)
    assert data.failed == 0
    assert "guide source copy not saved" in caplog.text


def test_reports_ids_missing_from_the_source(caplog: pytest.LogCaptureFixture) -> None:
    guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="Nope.tr"), NOW, 1, serving(SOURCE))
    assert [r.__dict__.get("ids") for r in caplog.records] == ["Nope.tr"]


def test_unparseable_source_falls_back() -> None:
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving("<tv><")).data)
    assert {item.findtext("title") for item in items} == {"atv"}


def test_parse_time_variants() -> None:
    assert guide.parse_time("20261011150000 +0300") == datetime(2026, 10, 11, 12, tzinfo=UTC)
    assert guide.parse_time("20261011150000+0300") == datetime(2026, 10, 11, 12, tzinfo=UTC)
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
    ).data
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
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving(source)).data)
    dizi = next(item for item in items if item.findtext("title") == "Dizi")
    assert [icon.get("src") for icon in dizi.findall("icon")] == ["https://own.example/x.jpg"]


ENTRY = (
    b'<programme start="20261011150000 +0000" stop="20261011160000 +0000" channel="ATV.tr"><title>A</title></programme>'
)


def bulk(path: Path, parts: list[bytes]) -> None:
    with gzip.open(path, "wb") as out:
        for part in parts:
            out.write(part)


def read_feed(path: Path) -> list[str]:
    found = guide._read(path, {"ATV.tr": ["201"]}, NOW - timedelta(days=1), NOW + timedelta(days=1))
    return [slot.element.findtext("title") or "" for slot in found["201"] if slot.element is not None]


def test_feed_with_many_small_entries_stays_small(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml.gz"
    keep = ENTRY
    bulk(path, [b"<tv>", b"<x/>" * 1_000_000, keep, b"</tv>"])
    tracemalloc.start()
    try:
        titles = read_feed(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert titles == ["A"]
    assert peak < 20 * 1024 * 1024


@pytest.mark.parametrize(
    ("parts", "message"),
    [
        ([b"<tv><programme>", b"<x/>" * 2000, b"</programme></tv>"], "more than 1000 parts"),
        ([b"<tv><programme><desc>", b"x" * 2_000_000, b"</desc></programme></tv>"], "larger than 64 KB"),
    ],
    ids=["parts", "text"],
)
def test_oversized_entries_stop_the_feed(tmp_path: Path, parts: list[bytes], message: str) -> None:
    path = tmp_path / "feed.xml.gz"
    bulk(path, parts)
    with pytest.raises(guide.FeedError, match=message):
        read_feed(path)


def test_feed_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "feed.xml.gz"
    entry = ENTRY
    bulk(path, [b"<tv>", entry * 20, b"</tv>"])
    monkeypatch.setattr(guide, "KEPT_LIMIT", 100)
    with pytest.raises(guide.FeedError, match="listings for these channels are larger"):
        read_feed(path)
    monkeypatch.setattr(guide, "KEPT_LIMIT", 10_000)
    monkeypatch.setattr(guide, "FEED_LIMIT", 100)
    with pytest.raises(guide.FeedError, match="larger than 0 MB unpacked"):
        read_feed(path)

    def copy_feed(url: str, destination: Path) -> None:
        destination.write_bytes(path.read_bytes())

    items = programmes(
        guide.build(lineup(xmltv="https://u:p@e.example.com/x?key=s", id="ATV.tr"), NOW, 1, copy_feed).data
    )
    assert {item.findtext("title") for item in items} == {"atv"}
    assert [getattr(record, "url", "") for record in caplog.records] == ["e.example.com"]


def test_guide_kept_in_memory_when_it_cannot_be_saved(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        cache = guide.Guide(folder / "guide.xml", 1)
        cache.rebuild(lineup(), NOW)
        assert cache.data is not None
        assert cache.built_at == NOW
        assert cache.write_error is not None
        assert cache.write_error.startswith(f"cannot write the guide to {folder}: ")
        folder.chmod(0o755)
        cache.rebuild(lineup(), NOW)
        assert cache.write_error is None
    finally:
        folder.chmod(0o755)


def test_huge_entry_is_stopped_while_reading(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml.gz"
    bulk(path, [b"<tv><programme><desc>", b"x" * 50_000_000, b"</desc></programme></tv>"])
    tracemalloc.start()
    try:
        with pytest.raises(guide.FeedError, match="larger than 64 KB"):
            read_feed(path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 5 * 1024 * 1024


def test_entry_of_many_small_parts_is_stopped(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml.gz"
    parts = b"<desc>" + b"x" * 80 + b"</desc>"
    bulk(path, [b"<tv>", ENTRY.replace(b"</programme>", parts * 900 + b"</programme>"), b"</tv>"])
    with pytest.raises(guide.FeedError, match="larger than 64 KB"):
        read_feed(path)


def test_entity_declarations_stop_the_feed(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml.gz"
    declared = b'<?xml version="1.0"?>\n<!DOCTYPE tv [\n<!ENTITY a "' + b"A" * 100 + b'">\n]>\n<tv>'
    bulk(path, [declared, ENTRY.replace(b">A<", b">&a;<"), b"</tv>"])
    with pytest.raises(guide.FeedError, match="declares its own XML entities"):
        read_feed(path)


def test_doctype_without_entities_is_read(tmp_path: Path) -> None:
    path = tmp_path / "feed.xml.gz"
    bulk(path, [b'<?xml version="1.0"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n<tv>', ENTRY, b"</tv>"])
    assert read_feed(path) == ["A"]


def test_out_of_range_times_are_skipped() -> None:
    source = SOURCE.replace(
        '<programme start="bad"',
        '<programme start="99991231225959 -1400" stop="99991231235959 -1400" channel="ATV.tr">'
        '<title>Far</title></programme>\n  <programme start="bad"',
    )
    items = programmes(guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, serving(source)).data)
    assert "Dizi" in {item.findtext("title") for item in items}
    assert guide.parse_time("99991231235959 -1400") is None


def test_damaged_gzip_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    def damaged(url: str, destination: Path) -> None:
        data = gzip.compress(SOURCE.encode())
        destination.write_bytes(data[:20] + b"\xff" * 40 + data[60:])

    built = guide.build(lineup(xmltv="https://e.example.com/x", id="ATV.tr"), NOW, 1, damaged)
    assert {item.findtext("title") for item in programmes(built.data)} == {"atv"}
    assert built.failed == 1
    assert "using placeholders" in caplog.text


def test_unreadable_copies_folder_falls_back(tmp_path: Path) -> None:
    def broken(url: str, destination: Path) -> None:
        raise FetchError("epg.example.com: HTTP 503")

    saved = tmp_path / "feeds"
    saved.mkdir()
    saved.chmod(0o000)
    try:
        built = guide.build(lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr"), NOW, 1, broken, saved)
    finally:
        saved.chmod(0o755)
    assert built.failed == 1


def test_source_without_listings_keeps_the_last_good_copy(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    saved = tmp_path / "feeds"
    channel = lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr")
    fresh = guide.build(channel, NOW, 1, serving(SOURCE), saved)
    empty = guide.build(channel, NOW, 1, serving("<tv></tv>"), saved)
    assert empty.data == fresh.data
    assert empty.failed == 0
    assert [record.__dict__.get("reason") for record in caplog.records] == ["no current listings"]
    assert guide.build(channel, NOW, 1, serving("<tv><"), saved).data == fresh.data


def test_sources_that_fail_the_same_way_again_are_not_retried_soon() -> None:
    def too_large(url: str, destination: Path) -> None:
        raise TooLargeError("epg.example.com: file is larger than 256 MB")

    channel = lineup(xmltv="https://epg.example.com/tr.xml", id="ATV.tr")
    assert guide.build(channel, NOW, 1, too_large).failed == 0
    declared = '<!DOCTYPE tv [<!ENTITY a "x">]>' + SOURCE.partition("?>")[2]
    assert guide.build(channel, NOW, 1, serving(declared)).failed == 0


def test_retry_downloads_only_the_failed_sources(tmp_path: Path) -> None:
    calls: list[str] = []
    failing = {"https://b.example.com/x"}

    def download(url: str, destination: Path) -> None:
        calls.append(url)
        if url in failing:
            raise FetchError("b.example.com: HTTP 503")
        destination.write_text(SOURCE)

    entries = [
        {"number": number, "name": name, "logo": "https://example.com/l.png", "hls": "https://cdn.example.com/x.m3u8",
         "guide": {"xmltv": url, "id": "ATV.tr"}}
        for number, name, url in [(201, "atv", "https://a.example.com/x"), (202, "show", "https://b.example.com/x")]
    ]  # fmt: skip
    both = channels.parse({"channel": entries})
    cache = guide.Guide(tmp_path / "guide.xml", 1, download)
    cache.rebuild(both, NOW)
    assert cache.failed_sources == 1
    first = cache.data
    failing.clear()
    calls.clear()
    cache.rebuild(both, NOW, retry=True)
    assert calls == ["https://b.example.com/x"]
    assert cache.failed_sources == 0
    assert cache.data != first
    calls.clear()
    reused = next(
        path for path in (tmp_path / "feeds").iterdir() if path.name == guide._saved_name("https://a.example.com/x")
    )
    os.utime(reused, (1_000_000_000, 1_000_000_000))
    cache.rebuild(both, NOW, retry=True)
    assert calls == []
    assert reused.stat().st_mtime == 1_000_000_000
    cache.rebuild(both, NOW)
    assert sorted(calls) == ["https://a.example.com/x", "https://b.example.com/x"]
