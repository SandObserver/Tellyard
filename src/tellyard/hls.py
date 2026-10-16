import re
import urllib.parse
from dataclasses import dataclass

# Unanchored, this takes quadratic time on a long line without "=".
ATTRIBUTE = re.compile(r'(?:^|,)[ \t]*([A-Z0-9-]+)=("[^"]*"|[^,]*)')
RESOLUTION = re.compile(r"^([0-9]+)x([0-9]+)$")
SCHEMES = ("http", "https")


class PlaylistError(Exception):
    pass


@dataclass(frozen=True)
class Variant:
    uri: str
    bandwidth: int
    height: int | None
    audio_group: str | None


@dataclass(frozen=True)
class Rendition:
    group: str
    uri: str | None
    default: bool


@dataclass(frozen=True)
class Choice:
    video: str
    audio: str | None
    height: int | None


def attributes(text: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in ATTRIBUTE.findall(text)}


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def parse_master(text: str, base: str) -> tuple[list[Variant], list[Rendition]]:
    lines = [line.strip() for line in text.splitlines()]
    if not lines or lines[0] != "#EXTM3U":
        raise PlaylistError("not an HLS playlist")
    variants: list[Variant] = []
    renditions: list[Rendition] = []
    pending: dict[str, str] | None = None
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = attributes(line.partition(":")[2])
        elif line.startswith("#EXT-X-MEDIA:"):
            if rendition := _rendition(attributes(line.partition(":")[2]), base):
                renditions.append(rendition)
        elif not line.startswith("#") and pending is not None:
            if uri := _join(base, line):
                variants.append(_variant(pending, uri))
            pending = None
    if not variants:
        raise PlaylistError("the master playlist lists no variants")
    return variants, renditions


def choose(variants: list[Variant], renditions: list[Rendition]) -> Choice:
    best = max(variants, key=lambda variant: (variant.height or 0, variant.bandwidth))
    audio = None
    if best.audio_group:
        group = [rendition for rendition in renditions if rendition.group == best.audio_group]
        if group:
            audio = next((rendition for rendition in group if rendition.default), group[0]).uri
    return Choice(best.uri, audio, best.height)


def _variant(info: dict[str, str], uri: str) -> Variant:
    resolution = RESOLUTION.match(info.get("RESOLUTION", ""))
    height = int(resolution.group(2)) if resolution else None
    raw = info.get("BANDWIDTH", "")
    bandwidth = int(raw) if raw.isascii() and raw.isdigit() else 0
    return Variant(uri, bandwidth, height, info.get("AUDIO") or None)


def _rendition(media: dict[str, str], base: str) -> Rendition | None:
    if media.get("TYPE") != "AUDIO" or not media.get("GROUP-ID"):
        return None
    # Without URI, the rendition is carried in the variant stream.
    uri = _join(base, media["URI"]) if media.get("URI") else None
    if media.get("URI") and uri is None:
        return None
    return Rendition(media["GROUP-ID"], uri, media.get("DEFAULT") == "YES")


def _join(base: str, reference: str) -> str | None:
    uri = urllib.parse.urljoin(base, reference)
    return uri if urllib.parse.urlsplit(uri).scheme in SCHEMES else None
