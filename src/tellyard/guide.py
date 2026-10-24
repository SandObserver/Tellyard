import contextlib
import copy
import hashlib
import io
import logging
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tellyard.channels import Channel, Lineup
from tellyard.net import FetchError, TooLargeError, download, hosts_of, open_maybe_gzip, safe_url

log = logging.getLogger(__name__)
XMLTV_TIME = "%Y%m%d%H%M%S %z"
HISTORY = timedelta(hours=2)
FEED_LIMIT = 1024 * 1024 * 1024
ENTRY_LIMIT = 64 * 1024
ENTRY_ELEMENTS = 1000
KEPT_LIMIT = 32 * 1024 * 1024
ENTITY_DECLARATION = b"<!ENTITY"

type Downloader = Callable[[str, Path], None]
# Strict XMLTV readers need this element order.
PROGRAMME_ORDER = (
    "title", "sub-title", "desc", "credits", "date", "category", "keyword", "language", "orig-language", "length",
    "icon", "url", "country", "episode-num", "video", "audio", "previously-shown", "premiere", "last-chance", "new",
    "subtitles", "rating", "star-rating", "review", "image",
)  # fmt: skip
MOVIE_CATEGORIES = frozenset({"movie", "movies", "film", "films", "sinema", "feature film"})


class FeedError(Exception):
    pass


FEED_ERRORS = (FetchError, FeedError, ET.ParseError, OSError, EOFError, zlib.error)
# These fail again on the next try. Only the others make the guide retry soon.
LASTING_ERRORS = (FeedError, TooLargeError)


@dataclass(frozen=True)
class Slot:
    start: datetime
    stop: datetime
    element: ET.Element | None


def format_time(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(XMLTV_TIME)


def is_movie(categories: list[str]) -> bool:
    return any(category.strip().casefold() in MOVIE_CATEGORIES for category in categories)


def parse_time(text: str) -> datetime | None:
    text = text.strip()
    digits, _, zone = text.partition(" ")
    try:
        moment = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    if not zone:
        return moment.replace(tzinfo=UTC)
    try:
        moment = datetime.strptime(f"{digits[:14]} {zone}", XMLTV_TIME)
        moment.astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    return moment


def fetch_feed(url: str, destination: Path) -> None:
    download(url, destination, hosts_of([url]))


@dataclass(frozen=True)
class Built:
    data: bytes
    failed: int
    loaded: frozenset[str] = frozenset()


def build(
    lineup: Lineup, now: datetime, days: int, downloader: Downloader = fetch_feed, saved: Path | None = None
) -> Built:
    start = now - HISTORY
    end = now + timedelta(days=days)
    schedules, failed, loaded = _external(lineup.channels, start, end, downloader, saved)
    root = ET.Element("tv", {"generator-info-name": "Tellyard"})
    for channel in lineup.channels:
        element = ET.SubElement(root, "channel", {"id": channel.guide_id})
        ET.SubElement(element, "display-name").text = channel.name
        ET.SubElement(element, "display-name").text = channel.number
        ET.SubElement(element, "icon", {"src": channel.logo})
    for channel in lineup.channels:
        for slot in _fill(channel, schedules.get(channel.number, []), start, end):
            root.append(_programme(channel, slot))
    ET.indent(root)
    return Built(ET.tostring(root, encoding="utf-8", xml_declaration=True), failed, loaded)


def _external(
    channels: Iterable[Channel], start: datetime, end: datetime, downloader: Downloader, saved: Path | None
) -> tuple[dict[str, list[Slot]], int, frozenset[str]]:
    wanted: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for channel in channels:
        if channel.xmltv:
            wanted[channel.xmltv.url][channel.xmltv.channel_id].append(channel.number)
    schedules: dict[str, list[Slot]] = defaultdict(list)
    failed = 0
    loaded: set[str] = set()
    for url, ids in wanted.items():
        kept = saved / _saved_name(url) if saved else None
        found, ok = _load(url, ids, (start, end), downloader, kept)
        if ok:
            loaded.add(url)
        else:
            failed += 1
        if found is None:
            continue
        for number, slots in found.items():
            schedules[number].extend(slots)
        missing = sorted(source_id for source_id, numbers in ids.items() if not any(n in found for n in numbers))
        if missing:
            log.warning("guide source has no current listings", extra={"url": safe_url(url), "ids": ",".join(missing)})
    if saved:
        _forget_saved(saved, {_saved_name(url) for url in wanted})
    return schedules, failed, frozenset(loaded)


def _load(
    url: str, ids: dict[str, list[str]], span: tuple[datetime, datetime], downloader: Downloader, kept: Path | None
) -> tuple[dict[str, list[Slot]] | None, bool]:
    with tempfile.TemporaryDirectory(prefix="tellyard-guide-") as scratch:
        path = Path(scratch) / "source.xml"
        try:
            downloader(url, path)
            found = _read(path, ids, *span)
        except FEED_ERRORS as error:
            return _read_saved(kept, url, ids, span, error), isinstance(error, LASTING_ERRORS)
        if not found:
            return _read_saved(kept, url, ids, span) or found, True
        if kept:
            _save(path, kept)
        return found, True


def _reusing(downloader: Downloader, saved: Path, reuse: frozenset[str]) -> Downloader:
    def fetch(url: str, destination: Path) -> None:
        kept = saved / _saved_name(url)
        if url in reuse and kept.is_file():
            shutil.copyfile(kept, destination)
        else:
            downloader(url, destination)

    return fetch


def _saved_name(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32] + ".xml"


def _read_saved(
    kept: Path | None,
    url: str,
    ids: dict[str, list[str]],
    span: tuple[datetime, datetime],
    error: Exception | None = None,
) -> dict[str, list[Slot]] | None:
    extra = {"url": safe_url(url), "reason": str(error) if error else "no current listings"}
    if kept:
        try:
            found = _read(kept, ids, *span)
            saved_at = datetime.fromtimestamp(kept.stat().st_mtime, UTC).isoformat(timespec="seconds")
        except FEED_ERRORS:
            pass
        else:
            if found:
                log.warning("guide source failed, using the last good copy", extra={**extra, "saved_at": saved_at})
                return found
    if error:
        log.warning("guide source failed, using placeholders", extra=extra)
    return None


def _save(path: Path, kept: Path) -> None:
    partial = kept.with_name(kept.name + ".part")
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, partial)
        partial.replace(kept)
    except OSError as error:
        with contextlib.suppress(OSError):
            partial.unlink(missing_ok=True)
        log.warning("guide source copy not saved", extra={"reason": error.strerror or str(error)})


def _forget_saved(saved: Path, keep: set[str]) -> None:
    with contextlib.suppress(OSError):
        for old in saved.iterdir():
            if old.name not in keep:
                old.unlink(missing_ok=True)


def _read(path: Path, ids: dict[str, list[str]], start: datetime, end: datetime) -> dict[str, list[Slot]]:
    # Feeds are untrusted and may be compressed. Every limit here bounds memory, not only time.
    found: dict[str, list[Slot]] = defaultdict(list)
    with open_maybe_gzip(path) as raw:
        stream = _Counted(raw)
        root: ET.Element | None = None
        depth = elements = began = kept = 0
        for event, element in ET.iterparse(stream, events=("start", "end")):
            stream.mark = stream.count
            if event == "start":
                depth += 1
                if root is None:
                    root = element
                    stream.prolog = False
                elif depth == 2:
                    elements, began = 0, stream.count
                elif (elements := elements + 1) > ENTRY_ELEMENTS:
                    raise FeedError(f"a guide entry has more than {ENTRY_ELEMENTS} parts")
                continue
            depth -= 1
            if depth != 1 or root is None:
                continue
            size = stream.count - began
            if size > ENTRY_LIMIT:
                raise FeedError(f"a guide entry is larger than {ENTRY_LIMIT // 1024} KB")
            numbers = ids.get(element.get("channel", "")) if element.tag == "programme" else None
            if numbers and _keep(element, numbers, start, end, found):
                kept += len(ET.tostring(element)) * len(numbers)
                if kept > KEPT_LIMIT:
                    raise FeedError(f"the listings for these channels are larger than {KEPT_LIMIT // 1024 // 1024} MB")
            root.clear()
    return found


def _keep(
    element: ET.Element, numbers: list[str], start: datetime, end: datetime, found: dict[str, list[Slot]]
) -> bool:
    begin = parse_time(element.get("start", ""))
    stop = parse_time(element.get("stop", ""))
    if not (begin and stop and begin < stop and stop > start and begin < end):
        return False
    for number in numbers:
        found[number].append(Slot(begin, stop, copy.deepcopy(element)))
    return True


class _Counted(io.RawIOBase):
    def __init__(self, stream: io.BufferedIOBase) -> None:
        self._stream = stream
        self.count = 0
        self.mark = 0
        self.prolog = True
        self._tail = b""

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size)
        self.count += len(data)
        if self.count > FEED_LIMIT:
            raise FeedError(f"the guide source is larger than {FEED_LIMIT // 1024 // 1024} MB unpacked")
        if self.count - self.mark > ENTRY_LIMIT + len(data):
            raise FeedError(f"a guide entry is larger than {ENTRY_LIMIT // 1024} KB")
        if self.prolog:
            # Entities declared in the source expand past every size limit here.
            window = self._tail + data
            if ENTITY_DECLARATION in window:
                raise FeedError("the guide source declares its own XML entities")
            self._tail = window[-len(ENTITY_DECLARATION) :]
        return data


def _fill(channel: Channel, slots: list[Slot], start: datetime, end: datetime) -> list[Slot]:
    ordered: list[Slot] = []
    cursor = start
    for slot in sorted(slots, key=lambda item: item.start):
        if slot.stop <= start:
            continue
        if ordered and slot.start < cursor:
            # Plex shows overlaps badly. End each show when the next show starts.
            previous = ordered.pop()
            if previous.start < slot.start:
                ordered.append(replace(previous, stop=slot.start))
            cursor = slot.start
        if slot.start > cursor:
            ordered += _placeholders(channel, cursor, slot.start)
        ordered.append(slot)
        cursor = slot.stop
    ordered += _placeholders(channel, cursor, end)
    return ordered


def _placeholders(channel: Channel, start: datetime, end: datetime) -> list[Slot]:
    block = timedelta(minutes=channel.placeholder.block_minutes)
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    boundary = epoch + ((start - epoch) // block + 1) * block
    slots: list[Slot] = []
    cursor = start
    while cursor < end:
        stop = min(boundary, end)
        slots.append(Slot(cursor, stop, None))
        cursor, boundary = stop, boundary + block
    return slots


def _programme(channel: Channel, slot: Slot) -> ET.Element:
    attributes = {"start": format_time(slot.start), "stop": format_time(slot.stop), "channel": channel.guide_id}
    if slot.element is not None:
        element = slot.element
        element.attrib.update(attributes)
        element.tail = None
    else:
        element = ET.Element("programme", attributes)
        ET.SubElement(element, "title").text = channel.placeholder.title
        ET.SubElement(element, "desc").text = channel.placeholder.description
    _decorate(element, channel, slot)
    return element


def _decorate(element: ET.Element, channel: Channel, slot: Slot) -> None:
    categories = [item.text or "" for item in element.findall("category")]
    if not categories and channel.placeholder.category:
        ET.SubElement(element, "category").text = channel.placeholder.category
        categories = [channel.placeholder.category]
    if not is_movie(categories) and element.find("episode-num") is None:
        # Plex shows an entry with no episode number as a movie.
        stamp = slot.start.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        ET.SubElement(element, "episode-num", {"system": "original-air-date"}).text = stamp
    rank = {tag: index for index, tag in enumerate(PROGRAMME_ORDER)}
    children = sorted(element, key=lambda child: rank.get(child.tag, len(rank)))
    for child in list(element):
        element.remove(child)
    element.extend(children)


class Guide:
    def __init__(self, path: Path, days: int, downloader: Downloader = fetch_feed) -> None:
        self._path = path
        self._days = days
        self._downloader = downloader
        self._data: bytes | None = None
        self.built_at: datetime | None = None
        self.write_error: str | None = None
        self.failed_sources = 0
        self._loaded: frozenset[str] = frozenset()
        if path.is_file():
            self._data = path.read_bytes()
            self.built_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)

    @property
    def data(self) -> bytes | None:
        return self._data

    def rebuild(self, lineup: Lineup, now: datetime | None = None, retry: bool = False) -> None:
        moment = now or datetime.now(UTC)
        saved = self._path.parent / "feeds"
        reuse = self._loaded if retry else frozenset()
        built = build(lineup, moment, self._days, _reusing(self._downloader, saved, reuse), saved)
        self._loaded = reuse | built.loaded
        data = built.data
        self._data = data
        self.built_at = moment
        self.failed_sources = built.failed
        partial = self._path.with_name(self._path.name + ".part")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(data)
            partial.replace(self._path)
        except OSError as error:
            self.write_error = f"cannot write the guide to {self._path.parent}: {error.strerror or error}"
            log.error("guide not saved", extra={"reason": self.write_error})
        else:
            self.write_error = None
        log.info("guide rebuilt", extra={"channels": len(lineup.channels), "size_kb": len(data) // 1024})
