# Contributing

## Setup

```sh
uv sync
```

Python 3.13. Running the service needs `ffmpeg` and `ffprobe`, and for YouTube channels `deno`, on the `PATH`.

## Run it locally

```sh
cp channels.example.toml channels.toml
CHANNELS_FILE=./channels.toml DATA_DIR=./data uv run tellyard serve
```

`channels.toml` and `data/` are ignored by git. Point Plex or VLC at `http://<your computer>:5004`, or open `http://localhost:5004/playlist.m3u`.

To run the checks before each commit and the tests before each push:

```sh
uvx pre-commit install --hook-type pre-commit --hook-type pre-push
```

## How it fits together

| Module | Job |
| --- | --- |
| `cli.py`, `settings.py` | Commands and settings from the environment. |
| `channels.py`, `store.py` | Read and check `channels.toml`; reload it when the file changes and keep the last good list. |
| `server.py` | The HTTP server: the HDHomeRun API, streams, guide, playlist and health. Counts tuners. |
| `resolve.py` | Turns a channel into a stream URL when someone tunes in: yt-dlp for `live`, a page fetch for `page`, an optional token service for `hls`. |
| `hls.py` | Reads HLS master playlists and picks the highest resolution and its audio. |
| `stream.py` | Runs ffmpeg for one viewer, watches for stalls and reconnects. |
| `guide.py` | Builds the XMLTV guide from XMLTV sources and placeholder blocks. |
| `check.py` | The `check` command: measures each channel's real resolution with ffprobe. |
| `net.py` | Every HTTP request, with size limits. |

## Before opening a PR

CI runs the same checks:

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

The tests need no internet connection and no ffmpeg. `pytest` measures coverage and fails below 85% or on any warning. CI also lints the workflows with actionlint and zizmor, builds the image, checks that it becomes healthy, plays a test stream through it, and scans it with Grype; a fixable critical vulnerability fails the build. It also builds the arm64 image under emulation and runs its tools once.

`tests/e2e.sh` is the test stream check. It builds a 1080p live stream with the image's own ffmpeg, serves it on a private Docker network, and checks that `tellyard check`, the line-up, the guide and `/stream/101` work, and that ffmpeg stops when the viewer leaves. It needs Docker but not the internet:

```sh
docker build -t tellyard:ci . && tests/e2e.sh tellyard:ci
```

Shared test doubles live in `tests/fakes.py`. Replace a method in a test with `monkeypatch.setattr`, not assignment.

Tests and examples use `example.com` addresses only. Never add a real channel, stream address, logo or token to the repository.

## Website

`site/` builds the landing page at https://tellyard.sandobserver.com. The headline comes from the bold tagline in `README.md`, the version from `CHANGELOG.md`, and the install steps from the code blocks under "Getting started" and "Channels" in `README.md`. The build fails if one of them is missing, so the page cannot drift from the README. Cloudflare Pages rebuilds the site on every push to `main`.

```sh
cd site && npm ci && npm run build
```

The channels on the page are fictional. The page names no video platform, and the six "Why Tellyard" cards also feed the page's FAQ structured data.

## Branches and PRs

- Branch: `type/short-description`, where type is `feat`, `fix`, `perf`, `security`, `refactor`, `docs`, `chore` or `release`.
- PR title: `Type: short description`, where Type is `Fix`, `Feature`, `Add`, `Improve`, `Refactor`, `Security` or `Docs`.
- User-visible changes go under `Unreleased` in [CHANGELOG.md](CHANGELOG.md), one line each.

## Releases

Semantic versioning. Move the `Unreleased` entries in [CHANGELOG.md](CHANGELOG.md) under the new version with its date, set `version` in `pyproject.toml`, merge, and push a `vX.Y.Z` tag. The tag runs the CI checks, then pushes `ghcr.io/sandobserver/tellyard:X.Y.Z`, `:X.Y` and `:latest` for amd64 and arm64, and publishes a GitHub release with the version's CHANGELOG section. The release stops if the tag, `pyproject.toml` and a dated CHANGELOG section do not agree. The image carries an SBOM and build provenance. Every tag is also pushed to `docker.io/sandobserver/tellyard`, and a stable release rewrites the Docker Hub page from `README.md` with `tools/dockerhub_description.py`. Docker Hub needs the repository secret `DOCKERHUB_TOKEN`, an access token with Read, Write and Delete. Docker Hub refuses to move an `X.Y.Z` tag, so a version cannot be published twice. The `Docker Hub page` workflow updates the page by hand.

### Unraid template

`templates/tellyard.xml` is the Unraid Community Applications template. It takes each setting's description and default from its row in the `README.md` Settings table, and its `Changes` from the newest dated `CHANGELOG.md` section. A new setting goes into the template by hand with its name, display and mask; then run `uv run python tools/unraid_template.py`. Release prep runs the script in every release pull request. `tests/test_unraid.py` fails when the template is out of date, a template default differs from the built-in default, or a template setting has no README row.

### Automatic yt-dlp releases

YouTube changes break older yt-dlp releases, so new yt-dlp versions ship on their own:

1. Dependabot opens a pull request for each new stable `yt-dlp` or `yt-dlp-ejs` daily. `yt-dlp-automerge.yml` turns on auto-merge, and it merges once the required checks pass.
2. When `uv.lock` on `main` changes a watched version, `release-prep.yml` runs `tools/release.py`: it bumps the version (the next beta while on betas, a minor release when Unreleased lists additions, a patch otherwise), dates the Unreleased section with an "Update yt-dlp to ..." line, and opens a release pull request with auto-merge on.
3. When `pyproject.toml` on `main` holds a new version with a dated CHANGELOG section, `release-tag.yml` pushes the tag, which runs `release.yml`.

The workflows act through a GitHub App, because pushes, merges and tags made with `GITHUB_TOKEN` start no further workflows. Its client ID and private key are stored as `RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY`, both as Actions secrets and as Dependabot secrets. Run **Release prep** by hand from the Actions tab to release whatever is in Unreleased.

A beta is a tag such as `v1.2.0-beta.1`, with `version = "1.2.0b1"` in `pyproject.toml` and a CHANGELOG heading `[1.2.0-beta.1]`. It publishes the image tags `1.2.0-beta.1` and `beta`, never `latest`, and a GitHub release marked as prerelease.
