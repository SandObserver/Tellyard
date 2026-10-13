import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NUMBER = re.compile(r"^\d{1,5}(\.\d{1,3})?$")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# Also matches JSON with escaped slashes.
DEFAULT_PAGE_PATTERN = r"https?:(?:\\?/){2}[^\s\"'<>]+?\.m3u8[^\s\"'<>]*"
CHANNEL_KEYS = frozenset(
    {
        "number",
        "name",
        "logo",
        "hls",
        "youtube",
        "page",
        "match",
        "headers",
        "token",
        "token_field",
        "guide",
        "min_height",
    }
)
GUIDE_KEYS = frozenset({"xmltv", "id", "title", "description", "category", "hours"})
SOURCE_KINDS = ("hls", "youtube", "page")
YOUTUBE_CHANNEL = re.compile(r"^https?://(?:www\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+)(?:/live)?/?$")
DEFAULT_BLOCK_HOURS = 2.0


class ChannelError(Exception):
    pass


type Headers = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HlsSource:
    url: str
    headers: Headers = ()
    token: str = ""
    token_field: str = "url"


@dataclass(frozen=True)
class YouTubeSource:
    url: str
    match: str = ""


@dataclass(frozen=True)
class PageSource:
    url: str
    match: str = ""
    headers: Headers = ()


type Source = HlsSource | YouTubeSource | PageSource


@dataclass(frozen=True)
class Placeholder:
    title: str
    description: str
    category: str
    block_minutes: int


@dataclass(frozen=True)
class XmltvGuide:
    url: str
    channel_id: str


@dataclass(frozen=True)
class Channel:
    number: str
    name: str
    logo: str
    source: Source
    placeholder: Placeholder
    xmltv: XmltvGuide | None
    min_height: int | None

    @property
    def guide_id(self) -> str:
        return f"tellyard.{self.number}"


@dataclass(frozen=True)
class Lineup:
    channels: tuple[Channel, ...]

    def find(self, number: str) -> Channel | None:
        return next((channel for channel in self.channels if channel.number == number), None)


def load(path: Path) -> Lineup:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ChannelError(f"cannot read {path}: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise ChannelError(f"{path} is not valid TOML: {error}") from error
    return parse(data)


def parse(data: Mapping[str, Any]) -> Lineup:
    if "defaults" in data:
        raise ChannelError("[defaults] is not supported: set MIN_HEIGHT in the environment")
    unknown = set(data) - {"guide_sources", "channel"}
    if unknown:
        raise ChannelError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
    sources = _guide_sources(data.get("guide_sources", {}))
    entries = data.get("channel", [])
    if not isinstance(entries, list) or not entries:
        raise ChannelError("add at least one [[channel]]")
    channels = tuple(_channel(entry, index, sources) for index, entry in enumerate(entries, 1))
    seen: set[str] = set()
    for channel in channels:
        if channel.number in seen:
            raise ChannelError(f"channel number {channel.number} is used more than once")
        seen.add(channel.number)
    return Lineup(tuple(sorted(channels, key=lambda channel: _number_key(channel.number))))


def _number_key(number: str) -> tuple[int, int]:
    major, _, minor = number.partition(".")
    return int(major), int(minor or 0)


def _guide_sources(value: Any) -> dict[str, str]:
    table = _table(value, "[guide_sources]")
    sources: dict[str, str] = {}
    for name, url in table.items():
        if not SOURCE_NAME.match(name):
            raise ChannelError(f"[guide_sources] name {name!r} must be lowercase letters, digits, - or _")
        sources[name] = _url(url, f"[guide_sources] {name}")
    return sources


def _channel(value: Any, index: int, sources: Mapping[str, str]) -> Channel:
    entry = _table(value, f"channel #{index}")
    raw_name = entry.get("name")
    where = f"channel {raw_name!r}" if isinstance(raw_name, str) and raw_name.strip() else f"channel #{index}"
    unknown = set(entry) - CHANNEL_KEYS
    if unknown:
        raise ChannelError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    name = _text(raw_name, f"{where} name")
    number = entry.get("number")
    number_text = str(number) if isinstance(number, int | str) and not isinstance(number, bool) else ""
    if not NUMBER.match(number_text):
        raise ChannelError(f"{where}: number must look like 101 or 101.1")
    logo = _url(entry.get("logo"), f"{where} logo")
    source = _source(entry, where)
    guide = _table(entry.get("guide", {}), f"{where} guide")
    placeholder, xmltv = _guide(guide, name, where, sources)
    raw_height = entry.get("min_height")
    height = None if raw_height is None else _positive_int(raw_height, f"{where} min_height")
    return Channel(number_text, name, logo, source, placeholder, xmltv, height)


def _source(entry: Mapping[str, Any], where: str) -> Source:
    kinds = [kind for kind in SOURCE_KINDS if kind in entry]
    if len(kinds) != 1:
        raise ChannelError(f"{where}: set exactly one of hls, youtube or page")
    kind = kinds[0]
    url = _url(entry[kind], f"{where} {kind}")
    headers = _headers(entry.get("headers", {}), where)
    match = entry.get("match", "")
    if not isinstance(match, str):
        raise ChannelError(f"{where}: match must be text")
    if kind != "hls" and ("token" in entry or "token_field" in entry):
        raise ChannelError(f"{where}: token only applies to hls sources")
    if kind == "youtube":
        return _youtube(url, match, headers, where)
    if kind == "page":
        return PageSource(url, match, headers)
    if match:
        raise ChannelError(f"{where}: match only applies to page and youtube sources")
    return _hls(entry, url, headers, where)


def _youtube(url: str, match: str, headers: Headers, where: str) -> YouTubeSource:
    if headers:
        raise ChannelError(f"{where}: headers are not supported for youtube sources")
    if match and not YOUTUBE_CHANNEL.match(url):
        raise ChannelError(f"{where}: match needs a channel URL such as https://www.youtube.com/@handle/live")
    return YouTubeSource(url, match.strip())


def _hls(entry: Mapping[str, Any], url: str, headers: Headers, where: str) -> HlsSource:
    if "token" not in entry:
        if "token_field" in entry:
            raise ChannelError(f"{where}: token_field needs token")
        return HlsSource(url, headers)
    token = _url(entry["token"], f"{where} token")
    field = _text(entry.get("token_field", "url"), f"{where} token_field")
    return HlsSource(url, headers, token, field)


def _guide(
    guide: Mapping[str, Any], name: str, where: str, sources: Mapping[str, str]
) -> tuple[Placeholder, XmltvGuide | None]:
    unknown = set(guide) - GUIDE_KEYS
    if unknown:
        raise ChannelError(f"{where} guide: unknown keys {', '.join(sorted(unknown))}")
    title = _text(guide.get("title", name), f"{where} guide title")
    description = _text(guide.get("description", f"Live on {name}."), f"{where} guide description")
    category = guide.get("category", "")
    if not isinstance(category, str):
        raise ChannelError(f"{where} guide category must be text")
    hours = guide.get("hours", DEFAULT_BLOCK_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, int | float) or not 0.5 <= hours <= 24:
        raise ChannelError(f"{where} guide hours must be between 0.5 and 24")
    minutes = round(hours * 60)
    if (24 * 60) % minutes:
        raise ChannelError(f"{where} guide hours must divide a day evenly, such as 1, 2, 3, 4, 6 or 12")
    placeholder = Placeholder(title, description, category.strip(), minutes)
    if "xmltv" not in guide:
        if "id" in guide:
            raise ChannelError(f"{where} guide: id needs xmltv")
        return placeholder, None
    reference = _text(guide["xmltv"], f"{where} guide xmltv")
    url = sources.get(reference) or _url(reference, f"{where} guide xmltv (a URL or a [guide_sources] name)")
    channel_id = _text(guide.get("id"), f"{where} guide id")
    return placeholder, XmltvGuide(url, channel_id)


def _headers(value: Any, where: str) -> Headers:
    table = _table(value, f"{where} headers")
    headers: list[tuple[str, str]] = []
    for key, header in table.items():
        if not HEADER_NAME.match(key) or not isinstance(header, str) or any(c in header for c in "\r\n"):
            raise ChannelError(f"{where}: header {key!r} is not valid")
        headers.append((key, header))
    return tuple(headers)


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChannelError(f"{where} must be a table")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChannelError(f"{where} must be non-empty text")
    return value.strip()


def _url(value: Any, where: str) -> str:
    text = _text(value, where)
    if not re.match(r"^https?://[^\s/]+", text):
        raise ChannelError(f"{where} must be an http or https URL")
    return text


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ChannelError(f"{where} must be a positive whole number")
    return value
