"""Copy the README settings and the newest release notes into templates/tellyard.xml.

Each Unraid variable gets its description and default from its row in the README Settings table.
The Changes element gets the newest dated CHANGELOG section, never Unreleased.

Run after a README settings change: uv run python tools/unraid_template.py. Release prep runs it in every release PR.
--check exits 1 when the template is out of date.
"""

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "tellyard.xml"
DATED = re.compile(r"^## \[([^\]]+)\] - (\d{4}-\d{2}-\d{2})$", re.M)
VARIABLE = re.compile(
    r'<Config Name="[^"]*" Target="([A-Z0-9_]+)" Default="[^"]*"[^>]*Type="Variable"[^>]*>[^<]*</Config>'
)


def attribute(text: str) -> str:
    return '"' + escape(text, {'"': "&quot;"}) + '"'


def plain(markdown: str) -> str:
    sentences = re.split(r"(?<=\.) ", markdown)
    text = " ".join(s for s in sentences if not s.startswith("See "))
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("`", "")


def readme_settings(readme: str) -> dict[str, tuple[str, str]]:
    table = readme.split("\n## Settings\n", 1)[1].split("\n## ", 1)[0]
    settings = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        name = re.fullmatch(r"`([A-Z][A-Z0-9_]+)`", cells[0])
        if name:
            default = re.fullmatch(r"`([^`]*)`", cells[2])
            settings[name.group(1)] = (plain(cells[1]), default.group(1) if default else "")
    return settings


def newest_release(changelog: str) -> str:
    heading = DATED.search(changelog)
    if heading is None:
        raise ValueError("CHANGELOG.md has no dated release section")
    body = changelog[heading.end() :].split("\n## ", 1)[0]
    lines = ["#" + line if line.startswith("### ") else line for line in body.splitlines()]
    lines = [line for line in lines if not re.match(r"^\[[^\]]+\]: https?://", line)]
    return f"### {heading.group(1)} - {heading.group(2)}\n\n" + "\n".join(lines).strip()


def render(template: str, readme: str, changelog: str) -> str:
    settings = readme_settings(readme)

    def config(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in settings:
            raise ValueError(f"README.md Settings has no row for {name}")
        description, default = settings[name]
        line = re.sub(r' Default="[^"]*"', lambda _: f" Default={attribute(default)}", match.group(0), count=1)
        line = re.sub(r""" Description=("[^"]*"|'[^']*')""", lambda _: f" Description={attribute(description)}", line)
        return re.sub(r">[^<]*</Config>$", lambda _: f">{escape(default)}</Config>", line)

    template = VARIABLE.sub(config, template)
    changes = f"<Changes>\n{escape(newest_release(changelog))}\n  </Changes>"
    return re.sub(r"<Changes>.*?</Changes>", lambda _: changes, template, count=1, flags=re.S)


def main(argv: list[str]) -> int:
    current = TEMPLATE.read_text()
    wanted = render(current, (ROOT / "README.md").read_text(), (ROOT / "CHANGELOG.md").read_text())
    if "--check" in argv:
        if current != wanted:
            print("templates/tellyard.xml is out of date. Run: uv run python tools/unraid_template.py")
            return 1
        return 0
    TEMPLATE.write_text(wanted)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
