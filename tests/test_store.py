import os
from pathlib import Path

import pytest

from tellyard.channels import ChannelError
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


def test_starts_empty_without_a_file_and_loads_it_later(
    tmp_path: Path, channels_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "config" / "channels.toml"
    store = ChannelStore(missing)
    assert store.current().channels == ()
    assert "no channel file yet" in caplog.text
    missing.parent.mkdir()
    missing.write_text(channels_file.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(store.current().channels) == 2
    assert store.version == 2


def test_a_bad_file_at_start_is_still_an_error(channels_file: Path) -> None:
    channels_file.write_text("[[channel]]\nnumber = 1\n", encoding="utf-8")
    with pytest.raises(ChannelError):
        ChannelStore(channels_file)
