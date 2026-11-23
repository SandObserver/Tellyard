import importlib.util
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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


NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


def files(uploaded: datetime, yanked: bool = False) -> list[dict[str, object]]:
    return [{"upload_time_iso_8601": uploaded.isoformat().replace("+00:00", "Z"), "yanked": yanked}]


def pypi(**ages: dict[str, timedelta | None]) -> Callable[[str], dict[str, object]]:
    def fetch(name: str) -> dict[str, object]:
        versions = ages[name.replace("-", "_")]
        return {
            "releases": {
                version: files(NOW - age) if age is not None else files(NOW - timedelta(days=9), yanked=True)
                for version, age in versions.items()
            }
        }

    return fetch


OLD = timedelta(days=3)


def test_newest_skips_yanked_and_pre_releases() -> None:
    project = pypi(yt_dlp={"2026.9.1": OLD, "2026.10.21": OLD, "2026.10.22": None, "2026.10.23.1.dev0": OLD})("yt-dlp")
    assert release.newest(project) == ("2026.10.21", NOW - OLD)
    project = pypi(yt_dlp={"2026.10.9": OLD, "2026.10.21": OLD, "2026.10.21.post1": OLD})("yt-dlp")
    assert release.newest(project) == ("2026.10.21.post1", NOW - OLD)
    with pytest.raises(ValueError, match="not a stable version"):
        release.stable_order("2026.10.23.1.dev0")
    assert release.newest({"releases": {"1.0": []}}) is None


def test_waits_while_the_newest_release_is_young() -> None:
    locked = {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}
    young = pypi(
        yt_dlp={"2026.8.19": OLD, "2026.10.21": OLD, "2026.10.24": timedelta(hours=20)}, yt_dlp_ejs={"0.8.0": OLD}
    )
    assert release.waiting(locked, young, NOW) == ["yt-dlp 2026.10.24 is not 24 hours old yet"]
    settled = pypi(yt_dlp={"2026.8.19": OLD, "2026.10.24": timedelta(hours=25)}, yt_dlp_ejs={"0.8.0": OLD})
    assert release.waiting(locked, settled, NOW) == []


def test_refuses_a_lock_with_a_young_or_unknown_version() -> None:
    fetch = pypi(yt_dlp={"2026.10.21": OLD, "2026.10.24": timedelta(hours=2)}, yt_dlp_ejs={"0.8.0": OLD})
    assert release.too_new({"yt-dlp": "2026.10.21", "yt-dlp-ejs": "0.8.0"}, fetch, NOW) == []
    assert release.too_new({"yt-dlp": "2026.10.24", "yt-dlp-ejs": "0.9.0"}, fetch, NOW) == [
        "yt-dlp 2026.10.24 is not a stable release 24 hours old",
        "yt-dlp-ejs 0.9.0 is not a stable release 24 hours old",
    ]


SECTION = "## [0.1.1] - 2026-10-25\n\n### Changed\n\n- Update yt-dlp to 2026.10.21.\n\n"
RELEASED = "## [Unreleased]\n\n" + SECTION + "## [0.1.0] - 2026-10-11\n"


def test_record_adds_the_released_section_below_unreleased(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG.replace("0.1.0-beta.1", "0.1.0"), encoding="utf-8")
    assert release.record(tmp_path, "0.1.1", RELEASED)
    assert "- Add another thing.\n\n" + SECTION + "## [0.1.0]" in (tmp_path / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    assert 'version = "0.1.1"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert not release.record(tmp_path, "0.1.1", RELEASED)


def test_record_keeps_a_higher_version_on_main(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.2.0b1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.2.0-beta.1] - 2026-10-24\n", encoding="utf-8")
    assert release.record(tmp_path, "0.1.1", RELEASED)
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == 'version = "0.2.0b1"\n'
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8").startswith("## [Unreleased]\n\n" + SECTION)
    assert release.prepare(tmp_path, "Update yt-dlp.", "2026-10-26") == "0.2.0-beta.2"


def test_record_keeps_a_beta_of_the_recorded_number(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.1.1b1"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.1.1-beta.1] - 2026-10-24\n", encoding="utf-8")
    assert release.record(tmp_path, "0.1.1", RELEASED)
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == 'version = "0.1.1b1"\n'
    assert release.prepare(tmp_path, "Update yt-dlp.", "2026-10-26", "0.1.1") == "0.1.2-beta.1"


def test_record_replaces_an_older_beta(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0b2"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.1.0-beta.2] - 2026-10-24\n", encoding="utf-8")
    assert release.record(tmp_path, "0.1.1", RELEASED)
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == 'version = "0.1.1"\n'


def test_record_refuses_a_release_without_a_section(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.1.0] - 2026-10-11\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"no dated section for 0\.1\.2"):
        release.record(tmp_path, "0.1.2", RELEASED)


@pytest.mark.parametrize(
    ("current", "above", "changelog", "expected"),
    [
        ("0.1.1", "0.1.2", "### Fixed\n\n- Fix.\n", "0.1.3"),
        ("0.1.1", "0.1.2", "### Added\n\n- Add.\n", "0.2.0"),
        ("0.1.1", "0.1.1", "### Fixed\n\n- Fix.\n", "0.1.2"),
        ("0.2.0b1", "0.1.2", "### Fixed\n\n- Fix.\n", "0.2.0-beta.2"),
        ("0.1.3b1", "0.1.3", "### Fixed\n\n- Fix.\n", "0.1.4-beta.1"),
    ],
)
def test_prepare_takes_a_number_above_existing_tags(
    tmp_path: Path, current: str, above: str, changelog: str, expected: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(f'version = "{current}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        f"## [Unreleased]\n\n{changelog}\n## [0.1.0] - 2026-10-11\n", encoding="utf-8"
    )
    assert release.prepare(tmp_path, None, "2026-10-26", above) == expected


def test_order_puts_betas_before_their_release() -> None:
    assert release.order("0.2.0b1") < release.order("0.2.0-beta.2") < release.order("0.2.0") < release.order("0.2.1")
    with pytest.raises(ValueError, match="cannot compare"):
        release.order("1.0.0rc1")


def test_age_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "uv.lock"
    path.write_text(lock(ytdlp="2026.10.21"), encoding="utf-8")
    young = pypi(yt_dlp={"2026.10.21": OLD, "2026.10.24": timedelta(hours=1)}, yt_dlp_ejs={"0.8.0": OLD})
    assert release.main(["ready", str(path)], young, NOW) == 0
    out = capsys.readouterr()
    assert out.out == "false\n"
    assert "Waiting: yt-dlp 2026.10.24" in out.err
    settled = pypi(yt_dlp={"2026.10.21": OLD}, yt_dlp_ejs={"0.8.0": OLD})
    assert release.main(["ready", str(path)], settled, NOW) == 0
    assert capsys.readouterr().out == "true\n"
    assert release.main(["verify", str(path)], settled, NOW) == 0
    path.write_text(lock(ytdlp="2026.10.24"), encoding="utf-8")
    assert release.main(["verify", str(path)], young, NOW) == 1
    assert "Refused: yt-dlp 2026.10.24" in capsys.readouterr().err


def test_record_and_pins_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n\n## [0.1.0] - 2026-10-11\n", encoding="utf-8")
    (tmp_path / "released.md").write_text(RELEASED, encoding="utf-8")
    command = ["record", "--root", str(tmp_path), "--version", "0.1.1", "--released", str(tmp_path / "released.md")]
    assert release.main(command) == 0
    assert release.main(command) == 0
    assert capsys.readouterr().out == "true\nfalse\n"
    (tmp_path / "uv.lock").write_text(lock(ytdlp="2026.10.21"), encoding="utf-8")
    assert release.main(["pins", str(tmp_path / "uv.lock")]) == 0
    assert capsys.readouterr().out == "yt-dlp==2026.10.21\nyt-dlp-ejs==0.8.0\n"
