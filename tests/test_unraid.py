import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

from tellyard.settings import Settings

ROOT = Path(__file__).parent.parent
TEMPLATE = ET.parse(ROOT / "templates" / "tellyard.xml").getroot()
CHANGELOG = """## [Unreleased]

### Added

- Not shipped yet.

## [1.2.0] - 2026-01-02

### Fixed

- Keep "quotes" & <brackets>.

## [1.1.0] - 2026-01-01

[Unreleased]: https://example.com/compare/v1.2.0...HEAD
"""
REQUIRED = ("Name", "Repository", "Overview", "Category", "Support", "Project", "TemplateURL", "Icon")


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("unraid_template", ROOT / "tools" / "unraid_template.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _tool()


def _configs(kind: str) -> dict[str, str]:
    return {c.attrib["Target"]: c.text or "" for c in TEMPLATE.iter("Config") if c.attrib["Type"] == kind}


def test_the_template_has_every_field_community_applications_needs() -> None:
    for name in REQUIRED:
        assert (TEMPLATE.findtext(name) or "").strip(), name
    assert ET.parse(ROOT / "ca_profile.xml").getroot().findtext("WebPage")


def test_the_template_defaults_are_the_service_defaults() -> None:
    assert Settings.from_env(_configs("Variable")) == Settings.from_env({})
    assert Settings.from_env(dict.fromkeys(_configs("Variable"), "")) == Settings.from_env({})


def test_the_template_matches_the_image_and_the_readme() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    folders = {re.findall(r"CHANNELS_FILE=(\S+)/", dockerfile)[0], *re.findall(r"DATA_DIR=(\S+)", dockerfile)}
    assert _configs("Path").keys() == folders
    assert _configs("Port").keys() == set(re.findall(r"EXPOSE (\d+)", dockerfile))
    readme = (ROOT / "README.md").read_text()
    assert [name for name in _configs("Variable") if f"`{name}`" not in readme] == []


def test_the_template_images_are_in_the_repository() -> None:
    raw = "https://raw.githubusercontent.com/SandObserver/Tellyard/main/"
    images = [TEMPLATE.findtext("Icon") or "", *(s.text or "" for s in TEMPLATE.iter("Screenshot"))]
    assert [url for url in images if not url.startswith(raw) or not (ROOT / url.removeprefix(raw)).is_file()] == []
    assert (TEMPLATE.findtext("Icon") or "").endswith(".png")


def test_the_template_matches_the_readme_settings_and_the_newest_release() -> None:
    template = (ROOT / "templates" / "tellyard.xml").read_text()
    readme = (ROOT / "README.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert TOOL.render(template, readme, changelog) == template, "Run: uv run python tools/unraid_template.py"
    assert TOOL.main(["--check"]) == 0


def test_a_readme_line_becomes_plain_text_without_links_to_other_pages() -> None:
    line = "Use **Apple** art from [TMDB](https://example.com) for `true`. See [Collections](docs/x.md#c). Next."
    assert TOOL.plain(line) == "Use Apple art from TMDB for true. Next."


def test_the_changes_are_the_newest_dated_release_only() -> None:
    changes = TOOL.newest_release(CHANGELOG)
    assert changes == '### 1.2.0 - 2026-01-02\n\n#### Fixed\n\n- Keep "quotes" & <brackets>.'


def test_a_changelog_without_a_release_is_refused() -> None:
    with pytest.raises(ValueError, match="no dated release"):
        TOOL.newest_release("## [Unreleased]\n")


def test_rendered_text_survives_xml_escaping() -> None:
    readme = '\n## Settings\n\n| `LOG_LEVEL` | Say "hi" & <b>. | `info` |\n\n## Next\n'
    template = (
        "<Container><Changes>old</Changes>"
        '<Config Name="Log Level" Target="LOG_LEVEL" Default="x" Mode="" Description="old" Type="Variable">x</Config>'
        "</Container>"
    )
    root = ET.fromstring(TOOL.render(template, readme, CHANGELOG))
    field = root.find("Config")
    assert field is not None
    assert (field.get("Description"), field.get("Default"), field.text) == ('Say "hi" & <b>.', "info", "info")
    assert (root.findtext("Changes") or "").strip().endswith('Keep "quotes" & <brackets>.')


def test_a_variable_missing_from_the_readme_is_refused() -> None:
    template = '<Config Name="X" Target="NOT_DOCUMENTED" Default="" Mode="" Description="" Type="Variable"></Config>'
    with pytest.raises(ValueError, match="NOT_DOCUMENTED"):
        TOOL.render(template, "\n## Settings\n\n## Next\n", CHANGELOG)
