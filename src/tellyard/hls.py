import re
import urllib.parse
from dataclasses import dataclass

ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
RESOLUTION = re.compile(r"^(\d+)x(\d+)$")


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
    uri: str
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
            media = attributes(line.partition(":")[2])
            if media.get("TYPE") == "AUDIO" and media.get("URI") and media.get("GROUP-ID"):
                uri = urllib.parse.urljoin(base, media["URI"])
                renditions.append(Rendition(media["GROUP-ID"], uri, media.get("DEFAULT") == "YES"))
        elif not line.startswith("#") and pending is not None:
            variants.append(_variant(pending, urllib.parse.urljoin(base, line)))
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
    bandwidth = int(info["BANDWIDTH"]) if info.get("BANDWIDTH", "").isdigit() else 0
    return Variant(uri, bandwidth, height, info.get("AUDIO") or None)
