import argparse
import json
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

VERSION_LINE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?$")
SECTION_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
UNRELEASED = "## [Unreleased]\n"
WATCHED = ("yt-dlp", "yt-dlp-ejs")
STABLE = re.compile(r"^(\d+(?:\.\d+)*)(?:\.post(\d+))?$")
PYPI = "https://pypi.org/pypi/{name}/json"
MIN_AGE = timedelta(hours=24)

type Project = dict[str, Any]
type Fetch = Callable[[str], Project]


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


def order(version: str) -> tuple[int, int, int, float]:
    match = PEP440.match(re.sub(r"-beta\.(\d+)$", r"b\1", version))
    if not match:
        raise ValueError(f"cannot compare version {version!r}")
    major, middle, patch, beta = match.groups()
    return int(major), int(middle), int(patch), float("inf") if beta is None else int(beta)


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


def prepare(root: Path, note: str | None, day: str, above: str | None = None) -> str:
    pyproject = root / "pyproject.toml"
    changelog = root / "CHANGELOG.md"
    project = pyproject.read_text(encoding="utf-8")
    match = VERSION_LINE.search(project)
    if not match:
        raise ValueError("pyproject.toml has no version line")
    text = changelog.read_text(encoding="utf-8")
    start, end = unreleased(text)
    minor = "Added" in sections(text[start:end])
    version = next_version(match.group(1), minor)
    if above and order(version) <= order(above):
        version = next_version(above, minor)
    changelog.write_text(date_changelog(text, version, day, note), encoding="utf-8")
    pyproject.write_text(VERSION_LINE.sub(f'version = "{version}"', project, count=1), encoding="utf-8")
    return display(version)


def dated_section(text: str, version: str) -> str | None:
    heading = re.search(rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text, re.MULTILINE)
    if not heading:
        return None
    end = text.find("\n## [", heading.end())
    return text[heading.start() : len(text) if end == -1 else end + 1].rstrip("\n") + "\n\n"


def record(root: Path, version: str, released: str) -> bool:
    pyproject = root / "pyproject.toml"
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    if dated_section(text, version):
        return False
    section = dated_section(released, version)
    if not section:
        raise ValueError(f"the released CHANGELOG.md has no dated section for {version}")
    _, end = unreleased(text)
    changelog.write_text(text[:end] + section + text[end:], encoding="utf-8")
    project = pyproject.read_text(encoding="utf-8")
    current = VERSION_LINE.search(project)
    if not current or order(current.group(1)) < order(version):
        pyproject.write_text(VERSION_LINE.sub(f'version = "{version}"', project, count=1), encoding="utf-8")
    return True


def uploads(project: Project) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    for version, files in project.get("releases", {}).items():
        if files and STABLE.match(version) and not any(item.get("yanked") for item in files):
            found[version] = min(datetime.fromisoformat(item["upload_time_iso_8601"]) for item in files)
    return found


def newest(project: Project) -> tuple[str, datetime] | None:
    found = uploads(project)
    if not found:
        return None
    version = max(found, key=stable_order)
    return version, found[version]


def stable_order(version: str) -> tuple[tuple[int, ...], int]:
    match = STABLE.match(version)
    if not match:
        raise ValueError(f"not a stable version {version!r}")
    return tuple(int(part) for part in match.group(1).split(".")), int(match.group(2) or -1)


def waiting(locked: dict[str, str], fetch: Fetch, now: datetime) -> list[str]:
    reasons = []
    for name in WATCHED:
        latest = newest(fetch(name))
        if latest and latest[0] != locked.get(name) and now - latest[1] < MIN_AGE:
            reasons.append(f"{name} {latest[0]} is not {MIN_AGE.total_seconds() / 3600:g} hours old yet")
    return reasons


def too_new(locked: dict[str, str], fetch: Fetch, now: datetime) -> list[str]:
    problems = []
    for name, version in locked.items():
        uploaded = uploads(fetch(name)).get(version)
        if uploaded is None or now - uploaded < MIN_AGE:
            problems.append(f"{name} {version} is not a stable release {MIN_AGE.total_seconds() / 3600:g} hours old")
    return problems


def fetch_pypi(name: str) -> Project:
    with urllib.request.urlopen(PYPI.format(name=name), timeout=30) as response:
        project: Project = json.load(response)
    return project


def main(argv: list[str] | None = None, fetch: Fetch = fetch_pypi, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    note = commands.add_parser("note", help="print the changelog line for a yt-dlp change between two uv.lock files")
    note.add_argument("old", type=Path)
    note.add_argument("new", type=Path)
    prep = commands.add_parser("prepare", help="bump the version and date the changelog; print the new version")
    prep.add_argument("--note", default="")
    prep.add_argument("--root", type=Path, default=Path())
    prep.add_argument("--above", help="a version the new one must be higher than")
    ready = commands.add_parser("ready", help="print true when no newer yt-dlp is still younger than MIN_AGE")
    ready.add_argument("lock", type=Path)
    verify = commands.add_parser("verify", help="fail when a yt-dlp version in uv.lock is younger than MIN_AGE")
    verify.add_argument("lock", type=Path)
    rec = commands.add_parser("record", help="copy a published release's CHANGELOG section below Unreleased")
    rec.add_argument("--version", required=True)
    rec.add_argument("--released", type=Path, required=True, help="CHANGELOG.md of the published release")
    rec.add_argument("--root", type=Path, default=Path())
    pins = commands.add_parser("pins", help="print the watched packages in uv.lock as name==version")
    pins.add_argument("lock", type=Path)
    args = parser.parse_args(argv)
    now = now or datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    if args.command == "prepare":
        print(prepare(args.root, args.note or None, day, args.above))
        return 0
    if args.command == "record":
        released = args.released.read_text(encoding="utf-8")
        print("true" if record(args.root, args.version, released) else "false")
        return 0
    if args.command == "note":
        old = lock_versions(args.old.read_text(encoding="utf-8"))
        line = change_note(old, lock_versions(args.new.read_text(encoding="utf-8")))
        print(line or "")
        return 0
    locked = lock_versions(args.lock.read_text(encoding="utf-8"))
    if args.command == "pins":
        for name, version in locked.items():
            print(f"{name}=={version}")
        return 0
    if args.command == "ready":
        reasons = waiting(locked, fetch, now)
        for reason in reasons:
            print(f"Waiting: {reason}.", file=sys.stderr)
        print("false" if reasons else "true")
        return 0
    problems = too_new(locked, fetch, now)
    for problem in problems:
        print(f"Refused: {problem}.", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
