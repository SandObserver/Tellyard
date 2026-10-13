import argparse
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

VERSION_LINE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?$")
SECTION_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
UNRELEASED = "## [Unreleased]\n"
WATCHED = ("yt-dlp", "yt-dlp-ejs")


def lock_versions(text: str) -> dict[str, str]:
    packages = tomllib.loads(text).get("package", [])
    return {item["name"]: item["version"] for item in packages if item.get("name") in WATCHED}


def change_note(old: dict[str, str], new: dict[str, str]) -> str | None:
    changed = [name for name in WATCHED if name in new and old.get(name) != new[name]]
    if not changed:
        return None
    return "Update " + " and ".join(f"{name} to {new[name]}" for name in changed) + "."


def next_version(current: str, minor: bool) -> str:
    match = PEP440.match(current)
    if not match:
        raise ValueError(f"cannot bump version {current!r}")
    major, middle, patch, beta = match.groups()
    if beta is not None:
        return f"{major}.{middle}.{patch}b{int(beta) + 1}"
    if minor:
        return f"{major}.{int(middle) + 1}.0"
    return f"{major}.{middle}.{int(patch) + 1}"


def display(version: str) -> str:
    return re.sub(r"b(\d+)$", r"-beta.\1", version)


def unreleased(text: str) -> tuple[int, int]:
    start = text.index(UNRELEASED) + len(UNRELEASED)
    end = text.find("\n## [", start)
    return start, len(text) if end == -1 else end + 1


def sections(body: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            found.setdefault(current, [])
        elif line.strip() and current:
            found[current].append(line)
    return found


def date_changelog(text: str, version: str, day: str, note: str | None) -> str:
    start, end = unreleased(text)
    found = sections(text[start:end])
    if note:
        found.setdefault("Changed", []).append(f"- {note}")
    if not found:
        raise ValueError("nothing to release: Unreleased is empty and there is no note")
    names = [name for name in SECTION_ORDER if name in found] + [name for name in found if name not in SECTION_ORDER]
    body = "\n\n".join(f"### {name}\n\n" + "\n".join(found[name]) for name in names)
    return f"{text[:start]}\n## [{display(version)}] - {day}\n\n{body}\n\n{text[end:].lstrip(chr(10))}"


def prepare(root: Path, note: str | None, day: str) -> str:
    pyproject = root / "pyproject.toml"
    changelog = root / "CHANGELOG.md"
    project = pyproject.read_text(encoding="utf-8")
    match = VERSION_LINE.search(project)
    if not match:
        raise ValueError("pyproject.toml has no version line")
    text = changelog.read_text(encoding="utf-8")
    start, end = unreleased(text)
    version = next_version(match.group(1), minor="Added" in sections(text[start:end]))
    changelog.write_text(date_changelog(text, version, day, note), encoding="utf-8")
    pyproject.write_text(VERSION_LINE.sub(f'version = "{version}"', project, count=1), encoding="utf-8")
    return display(version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    note = commands.add_parser("note", help="print the changelog line for a yt-dlp change between two uv.lock files")
    note.add_argument("old", type=Path)
    note.add_argument("new", type=Path)
    prep = commands.add_parser("prepare", help="bump the version and date the changelog; print the new version")
    prep.add_argument("--note", default="")
    prep.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args(argv)
    if args.command == "note":
        old = lock_versions(args.old.read_text(encoding="utf-8"))
        line = change_note(old, lock_versions(args.new.read_text(encoding="utf-8")))
        print(line or "")
        return 0
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    print(prepare(args.root, args.note or None, day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
