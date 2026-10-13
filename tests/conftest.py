import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tellyard.settings import Settings

BASIC = """
[[channel]]
number = 101
name = "News One"
logo = "https://example.com/news.png"
youtube = "https://www.youtube.com/@newsone/live"
[channel.guide]
category = "News"

[[channel]]
number = 201
name = "Drama"
logo = "https://example.com/drama.png"
hls = "https://cdn.example.com/drama/master.m3u8"
headers = { Referer = "https://drama.example.com/" }
"""


@pytest.fixture(autouse=True)
def root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def channels_file(tmp_path: Path) -> Path:
    path = tmp_path / "channels.toml"
    path.write_text(BASIC, encoding="utf-8")
    return path


@pytest.fixture
def make_settings(tmp_path: Path, channels_file: Path) -> Callable[..., Settings]:
    def make(**env: str) -> Settings:
        base = {"CHANNELS_FILE": str(channels_file), "DATA_DIR": str(tmp_path / "data"), **env}
        return Settings.from_env(base)

    return make
