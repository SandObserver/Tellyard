"""Print README.md as the Docker Hub repository overview.

Docker Hub renders the text away from the repository, shows HTML as literal text and has no relative links.
The header is replaced, every link is made absolute, code blocks name the Docker Hub image, and an
Image tags section is added for the current version.

Usage: python3 tools/dockerhub_description.py > dockerhub.md
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "SandObserver/Tellyard"
BLOB = f"https://github.com/{REPO}/blob/main"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
GHCR_IMAGE = "ghcr.io/sandobserver/tellyard"
HUB_IMAGE = "sandobserver/tellyard"
MAX_LENGTH = 25000
GHCR_PAGE = f"https://github.com/{REPO}/pkgs/container/tellyard"
RELEASES = f"https://github.com/{REPO}/releases"
BANNER = "Tellyard beside a live TV guide with five example channels"

HEADER = f"""# Tellyard

**A live TV tuner you'll forget is running.**

[![ghcr.io](https://img.shields.io/badge/ghcr.io-tellyard-2496ED?logo=github&logoColor=white)]({GHCR_PAGE})
[![Docker Hub](https://img.shields.io/badge/docker%20hub-tellyard-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/r/{HUB_IMAGE})
[![Latest release](https://img.shields.io/github/v/release/{REPO})]({RELEASES})

![{BANNER}]({RAW}/docs/social-card.png)

Website: **[tellyard.sandobserver.com](https://tellyard.sandobserver.com)**

Source and issues: **[github.com/{REPO}](https://github.com/{REPO})**
"""


def tags(version: str) -> str:
    minor = ".".join(version.split(".")[:2])
    return f"""## Image tags

| Tag | Points to |
| --- | --- |
| `latest` | The newest stable release. |
| `{minor}` | The newest patch of a minor version. |
| `{version}` | One exact release. It never changes. |
| `beta` | The newest beta, for testing. |

Every tag is built for `linux/amd64` and `linux/arm64`. The same image is on [{GHCR_IMAGE}]({GHCR_PAGE}).
Release notes are on [GitHub Releases]({RELEASES}).

"""


def absolute(match: re.Match[str]) -> str:
    bang, text, target = match.groups()
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return match.group(0)
    if target.startswith("#"):
        return f"{bang}[{text}](https://github.com/{REPO}{target})"
    return f"{bang}[{text}]({RAW if bang else BLOB}/{target})"


def description(readme: str, version: str) -> str:
    first = readme.index("\n## ")
    intro = readme[:first].rstrip().rsplit("\n\n", 1)[-1]
    md = f"{HEADER}\n{intro}\n{readme[first:]}"
    if "\n## Unraid\n" not in md:
        raise ValueError("README.md has no Unraid section to put the image tags before")
    md = md.replace("\n## Unraid\n", f"\n{tags(version)}## Unraid\n", 1)
    md = re.sub(r"<details>\n<summary>([^<]+)</summary>\n", r"#### \1\n", md).replace("\n</details>\n", "\n")
    md = re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", absolute, md)
    md = re.sub(r"```[\s\S]*?```", lambda block: block.group(0).replace(GHCR_IMAGE, HUB_IMAGE), md)
    md = md.rstrip() + "\n"
    if len(md) > MAX_LENGTH:
        raise ValueError(f"{len(md)} characters, over Docker Hub's {MAX_LENGTH}. Shorten README.md")
    return md


def main() -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    sys.stdout.write(description((ROOT / "README.md").read_text(), version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
