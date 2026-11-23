import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parent.parent


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dockerhub_description", ROOT / "tools" / "dockerhub_description.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _tool()
PAGE: str = TOOL.description((ROOT / "README.md").read_text(), "1.2.3")
PROSE = re.sub(r"```[\s\S]*?```", "", PAGE)


def test_the_page_fits_docker_hub() -> None:
    assert len(PAGE) <= TOOL.MAX_LENGTH


def test_every_link_and_image_is_absolute() -> None:
    targets = re.findall(r"\]\(([^)\s]+)\)", PAGE)
    assert targets
    assert [t for t in targets if not t.startswith("https://")] == []


def test_the_page_has_no_html_outside_code() -> None:
    assert re.findall(r"<[a-z/][^>]*>", PROSE) == []


def test_commands_pull_from_docker_hub() -> None:
    blocks = "".join(re.findall(r"```[\s\S]*?```", PAGE))
    assert "sandobserver/tellyard:" in blocks
    assert "ghcr.io/sandobserver/tellyard" not in blocks


def test_the_tags_name_the_current_version() -> None:
    assert "| `1.2` |" in PAGE
    assert "| `1.2.3` |" in PAGE
    assert PAGE.index("## Image tags") < PAGE.index("## Unraid")


def test_a_readme_without_an_unraid_section_is_refused() -> None:
    with pytest.raises(ValueError, match="Unraid"):
        TOOL.description("# Tellyard\n\nIntro.\n\n## Settings\n", "1.2.3")


def test_a_readme_over_the_limit_is_refused() -> None:
    readme = "# Tellyard\n\nIntro.\n\n## Unraid\n\n" + "x" * TOOL.MAX_LENGTH
    with pytest.raises(ValueError, match="Shorten"):
        TOOL.description(readme, "1.2.3")
