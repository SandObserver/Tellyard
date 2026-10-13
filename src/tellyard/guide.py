import copy
import logging
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tellyard.channels import Channel, Lineup
from tellyard.net import FetchError, download, open_maybe_gzip

log = logging.getLogger(__name__)
XMLTV_TIME = "%Y%m%d%H%M%S %z"
HISTORY = timedelta(hours=2)

type Downloader = Callable[[str, Path], None]
# Strict XMLTV readers need this element order.
PROGRAMME_ORDER = (
    "title", "sub-title", "desc", "credits", "date", "category", "keyword", "language", "orig-language", "length",
    "icon", "url", "country", "episode-num", "video", "audio", "previously-shown", "premiere", "last-chance", "new",
    "subtitles", "rating", "star-rating", "review", "image",
)  # fmt: skip
MOVIE_CATEGORIES = frozenset({"movie", "movies", "film", "films", "sinema", "feature film"})


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
        return datetime.strptime(f"{digits[:14]} {zone}", XMLTV_TIME)
    except ValueError:
        return None


def build(lineup: Lineup, now: datetime, days: int, downloader: Downloader = download) -> bytes:
    start = now - HISTORY
    end = now + timedelta(days=days)
    schedules = _external(lineup.channels, start, end, downloader)
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
    data: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return data


def _external(
    channels: Iterable[Channel], start: datetime, end: datetime, downloader: Downloader
) -> dict[str, list[Slot]]:
    wanted: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for channel in channels:
        if channel.xmltv:
            wanted[channel.xmltv.url][channel.xmltv.channel_id].append(channel.number)
    schedules: dict[str, list[Slot]] = defaultdict(list)
    with tempfile.TemporaryDirectory(prefix="tellyard-guide-") as scratch:
        for index, (url, ids) in enumerate(wanted.items()):
            path = Path(scratch) / f"source-{index}.xml"
            try:
                downloader(url, path)
                found = _read(path, ids, start, end)
            except (FetchError, ET.ParseError, OSError, EOFError) as error:
                log.warning("guide source failed, using placeholders", extra={"url": url, "reason": str(error)})
                continue
            for number, slots in found.items():
                schedules[number].extend(slots)
            missing = sorted(source_id for source_id, numbers in ids.items() if not any(n in found for n in numbers))
            if missing:
                log.warning("guide source has no current listings", extra={"url": url, "ids": ",".join(missing)})
    return schedules


def _read(path: Path, ids: dict[str, list[str]], start: datetime, end: datetime) -> dict[str, list[Slot]]:
    found: dict[str, list[Slot]] = defaultdict(list)
    with open_maybe_gzip(path) as stream:
        context = ET.iterparse(stream, events=("start", "end"))
        root: ET.Element | None = None
        for event, element in context:
            if root is None and event == "start":
                root = element
            if event != "end" or element.tag not in {"programme", "channel"}:
                continue
            numbers = ids.get(element.get("channel", "")) if element.tag == "programme" else None
            if numbers:
                begin = parse_time(element.get("start", ""))
                stop = parse_time(element.get("stop", ""))
                if begin and stop and begin < stop and stop > start and begin < end:
                    for number in numbers:
                        found[number].append(Slot(begin, stop, copy.deepcopy(element)))
            if root is not None:
                root.clear()
    return found


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
    def __init__(self, path: Path, days: int, downloader: Downloader = download) -> None:
        self._path = path
        self._days = days
        self._downloader = downloader
        self._data: bytes | None = None
        self.built_at: datetime | None = None
        if path.is_file():
            self._data = path.read_bytes()
            self.built_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)

    @property
    def data(self) -> bytes | None:
        return self._data

    def rebuild(self, lineup: Lineup, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        data = build(lineup, moment, self._days, self._downloader)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_name(self._path.name + ".part")
        partial.write_bytes(data)
        partial.replace(self._path)
        self._data = data
        self.built_at = moment
        log.info("guide rebuilt", extra={"channels": len(lineup.channels), "size_kb": len(data) // 1024})
