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


BAD = "[[channel]]\nnumber = 1\n"


def test_saves_a_copy_of_each_good_file(channels_file: Path, tmp_path: Path) -> None:
    saved = tmp_path / "data" / "channels.last-good.toml"
    store = ChannelStore(channels_file, saved)
    assert saved.read_text(encoding="utf-8") == channels_file.read_text(encoding="utf-8")
    assert saved.stat().st_mode & 0o777 == 0o600
    good = channels_file.read_text(encoding="utf-8").replace("Drama", "Comedy")
    touch_later(channels_file, good)
    store.current()
    assert saved.read_text(encoding="utf-8") == good
    touch_later(channels_file, BAD)
    store.current()
    assert saved.read_text(encoding="utf-8") == good


def test_a_bad_file_at_start_uses_the_last_good_copy(
    channels_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    saved = tmp_path / "channels.last-good.toml"
    ChannelStore(channels_file, saved)
    channels_file.write_text(BAD, encoding="utf-8")
    store = ChannelStore(channels_file, saved)
    assert [c.name for c in store.current().channels] == ["News One", "Drama"]
    assert store.error is not None and "name must be non-empty" in store.error
    assert "starting with the last good copy" in caplog.text
    assert saved.read_text(encoding="utf-8") != BAD


def test_fixing_the_file_clears_the_error(channels_file: Path, tmp_path: Path) -> None:
    good = channels_file.read_text(encoding="utf-8")
    store = ChannelStore(channels_file, tmp_path / "copy.toml")
    touch_later(channels_file, BAD)
    store.current()
    assert store.error
    touch_later(channels_file, good)
    store.current()
    assert store.error is None


def test_a_bad_file_without_a_good_copy_is_an_error(channels_file: Path, tmp_path: Path) -> None:
    channels_file.write_text(BAD, encoding="utf-8")
    with pytest.raises(ChannelError, match="name"):
        ChannelStore(channels_file, tmp_path / "none.toml")
    broken = tmp_path / "broken.toml"
    broken.write_text("not toml [", encoding="utf-8")
    with pytest.raises(ChannelError, match="name"):
        ChannelStore(channels_file, broken)


def test_a_copy_that_cannot_be_saved_is_logged(
    channels_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    store = ChannelStore(channels_file, blocked / "copy.toml")
    assert len(store.current().channels) == 2
    assert "channel file copy not saved" in caplog.text
