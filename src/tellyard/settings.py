import hashlib
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

DEVICE_ID = re.compile(r"^[0-9A-F]{8}$")
BASE_URL = re.compile(r"^https?://[^\s/]+$")


class SettingsError(Exception):
    pass


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Settings:
    channels_file: Path
    data_dir: Path
    port: int
    base_url: str
    tuners: int
    device_name: str
    device_id: str
    guide_days: int
    guide_refresh_hours: float
    ytdlp: str
    ytdlp_args: tuple[str, ...]
    ffmpeg: str
    log_level: LogLevel
    min_height: int | None
    heartbeat_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        name = env.get("DEVICE_NAME", "Tellyard").strip() or "Tellyard"
        device_id = env.get("DEVICE_ID", "").strip().upper() or _device_id(name)
        if not DEVICE_ID.match(device_id):
            raise SettingsError("DEVICE_ID must be 8 hexadecimal characters")
        base_url = env.get("BASE_URL", "").strip().rstrip("/")
        if base_url and not BASE_URL.match(base_url):
            raise SettingsError("BASE_URL must look like http://192.168.1.10:5004, with no path")
        try:
            level = LogLevel(env.get("LOG_LEVEL", "").strip().lower() or "info")
        except ValueError:
            raise SettingsError("LOG_LEVEL must be debug, info, warning or error") from None
        heartbeat_url = env.get("HEARTBEAT_URL", "").strip()
        if heartbeat_url and not heartbeat_url.startswith(("http://", "https://")):
            raise SettingsError("HEARTBEAT_URL must start with http:// or https://")
        try:
            ytdlp_args = tuple(shlex.split(env.get("YTDLP_ARGS", "")))
        except ValueError as error:
            raise SettingsError(f"YTDLP_ARGS cannot be parsed: {error}") from error
        return cls(
            channels_file=Path(env.get("CHANNELS_FILE", "/config/channels.toml")),
            data_dir=Path(env.get("DATA_DIR", "/data")),
            port=_int(env, "LISTEN_PORT", 5004, 1, 65535),
            base_url=base_url,
            tuners=_int(env, "TUNERS", 4, 1, 32),
            device_name=name,
            device_id=device_id,
            guide_days=_int(env, "GUIDE_DAYS", 3, 1, 14),
            guide_refresh_hours=float(_int(env, "GUIDE_REFRESH_HOURS", 6, 1, 48)),
            ytdlp=env.get("YTDLP", "yt-dlp"),
            ytdlp_args=ytdlp_args,
            ffmpeg=env.get("FFMPEG", "ffmpeg"),
            log_level=level,
            min_height=_min_height(env),
            heartbeat_url=heartbeat_url,
        )


def _device_id(name: str) -> str:
    # Plex identifies the tuner by this ID. Keep the default stable across restarts.
    return hashlib.sha256(name.encode()).hexdigest()[:8].upper()


def _min_height(env: Mapping[str, str]) -> int | None:
    raw = env.get("MIN_HEIGHT", "").strip().lower()
    if raw in {"", "off"}:
        return None
    return _int(env, "MIN_HEIGHT", 0, 144, 4320)


def _int(env: Mapping[str, str], key: str, default: int, low: int, high: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SettingsError(f"{key} must be a whole number") from None
    if not low <= value <= high:
        raise SettingsError(f"{key} must be between {low} and {high}")
    return value
