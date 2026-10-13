import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_tool", ROOT / "tools" / "release.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_tool"] = module
    spec.loader.exec_module(module)
    return module


release = load()

LOCK = """version = 1
[[package]]
name = "yt-dlp"
version = "{ytdlp}"
[[package]]
name = "yt-dlp-ejs"
version = "{ejs}"
[[package]]
name = "certifi"
version = "2026.7.22"
"""

CHANGELOG = """# Changelog

## [Unreleased]

### Fixed

- Fix one thing.

### Added

- Add another thing.

## [0.1.0-beta.1] - 2026-10-11

First version.
"""


def lock(ytdlp: str = "2026.8.19", ejs: str = "0.8.0") -> str:
    return LOCK.format(ytdlp=ytdlp, ejs=ejs)


def test_reads_the_watched_versions() -> None:
    assert release.lock_versions(lock()) == {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}


@pytest.mark.parametrize(
    ("new", "note"),
    [
        (lock(), None),
        (lock(ytdlp="2026.10.20"), "Update yt-dlp to 2026.10.20."),
        (lock(ytdlp="2026.10.20", ejs="0.9.0"), "Update yt-dlp to 2026.10.20 and yt-dlp-ejs to 0.9.0."),
    ],
)
def test_change_note(new: str, note: str | None) -> None:
    assert release.change_note(release.lock_versions(lock()), release.lock_versions(new)) == note


@pytest.mark.parametrize(
    ("current", "minor", "expected"),
    [("0.1.0b1", True, "0.1.0b2"), ("0.1.0", False, "0.1.1"), ("0.1.3", True, "0.2.0"), ("1.4.9", False, "1.4.10")],
)
def test_next_version(current: str, minor: bool, expected: str) -> None:
    assert release.next_version(current, minor) == expected


def test_next_version_rejects_unknown_shapes() -> None:
    with pytest.raises(ValueError, match="cannot bump"):
        release.next_version("1.0.0rc1", False)


def test_display_spells_betas_like_tags() -> None:
    assert release.display("0.1.0b2") == "0.1.0-beta.2"
    assert release.display("1.0.0") == "1.0.0"


def test_dates_unreleased_and_adds_the_note_in_order() -> None:
    text = release.date_changelog(CHANGELOG, "0.1.0b2", "2026-10-20", "Update yt-dlp to 2026.10.20.")
    assert text.startswith("# Changelog\n\n## [Unreleased]\n\n## [0.1.0-beta.2] - 2026-10-20\n\n### Added\n")
    assert text.index("### Added") < text.index("### Changed") < text.index("### Fixed")
    assert "### Changed\n\n- Update yt-dlp to 2026.10.20.\n" in text
    assert text.endswith("## [0.1.0-beta.1] - 2026-10-11\n\nFirst version.\n")


def test_refuses_an_empty_release() -> None:
    empty = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - 2026-10-11\n\nFirst.\n"
    with pytest.raises(ValueError, match="nothing to release"):
        release.date_changelog(empty, "0.1.1", "2026-10-20", None)


def test_prepare_writes_both_files(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG.replace("0.1.0-beta.1", "0.1.0"), encoding="utf-8")
    assert release.prepare(tmp_path, "Update yt-dlp to 2026.10.20.", "2026-10-20") == "0.2.0"
    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert "## [0.2.0] - 2026-10-20" in (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old, new = tmp_path / "old.lock", tmp_path / "new.lock"
    old.write_text(lock(), encoding="utf-8")
    new.write_text(lock(ytdlp="2026.10.20"), encoding="utf-8")
    assert release.main(["note", str(old), str(new)]) == 0
    assert capsys.readouterr().out == "Update yt-dlp to 2026.10.20.\n"
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0b1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert release.main(["prepare", "--root", str(tmp_path), "--note", "Update yt-dlp to 2026.10.20."]) == 0
    assert capsys.readouterr().out == "0.1.0-beta.2\n"
