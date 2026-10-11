import os
from pathlib import Path

import pytest

from tellyard.store import ChannelStore


def touch_later(path: Path, text: str) -> None:
    stat = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_reloads_after_a_change(channels_file: Path) -> None:
    store = ChannelStore(channels_file)
    assert [c.name for c in store.current().channels] == ["News One", "Drama"]
    touch_later(channels_file, channels_file.read_text(encoding="utf-8").replace("Drama", "Comedy"))
    assert [c.name for c in store.current().channels] == ["News One", "Comedy"]
    assert store.version == 2


def test_keeps_the_last_good_list_on_a_mistake(channels_file: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = ChannelStore(channels_file)
    touch_later(channels_file, "[[channel]]\nnumber = 1\n")
    assert len(store.current().channels) == 2
    assert store.version == 1
    assert "keeping the previous channels" in caplog.text


def test_survives_the_file_disappearing(channels_file: Path) -> None:
    store = ChannelStore(channels_file)
    channels_file.unlink()
    assert len(store.current().channels) == 2
