# Contributing

## Setup

```sh
uv sync
```

Python 3.13. Running the service needs `ffmpeg`, and for YouTube channels `deno`, on the `PATH`.

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
| `check.py` | The `check` command: measures each channel's real resolution with ffmpeg. |
| `net.py` | Every HTTP request, with size limits. |

## Before opening a PR

CI runs the same checks:

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

The tests need no internet connection and no ffmpeg. `pytest` measures coverage and fails below 85% or on any warning. CI also lints the workflows with actionlint and zizmor, builds the image, checks that it becomes healthy, plays a test stream through it, and scans it with Grype; a fixable critical vulnerability fails the build. It also builds the arm64 image under emulation and runs its tools once.

`tests/e2e.sh` is the test stream check. It builds a 1080p live stream with the full ffmpeg image from the Dockerfile's `ffmpeg-full` stage, serves it on a private Docker network, and checks that `tellyard check`, the line-up, the guide and `/stream/101` work, and that ffmpeg stops when the viewer leaves. It needs Docker, and the internet only to pull that ffmpeg image:

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

YouTube changes break older yt-dlp releases, so new yt-dlp versions ship on their own. They never ship unreleased work from `main`.

1. Every 6 hours, `yt-dlp-release.yml` looks for a newer stable `yt-dlp` or `yt-dlp-ejs` on PyPI. Yanked and pre-release versions do not count. It waits until the newest version is 24 hours old, so a release that is pulled or fixed again the same day never ships.
2. It takes the newest published release, updates yt-dlp in `uv.lock`, and dates a patch release with an "Update yt-dlp to ..." line. The number is above every existing tag. Pushing that tag runs `release.yml`, which runs every CI job before it publishes.
3. When `release.yml` succeeds, the workflow opens an auto-merging `Release: X.Y.Z` pull request that copies the release's CHANGELOG section and yt-dlp versions to `main`. It sets the version on `main` only when that is lower. A beta in progress keeps its version. If the automatic release took the beta's number, the next beta moves to the next patch number.

The workflow waits while a hand release is open or not yet tagged, while a release run is not finished, and while the newest published release is not recorded on `main`. Each run first records the newest published release if `main` does not have it. A release that failed is not built again with the same yt-dlp. Check its Release run.

Dependabot ignores yt-dlp. Run the workflow by hand from `main` with `dry-run` to see what it would record and release.

Other releases are made by hand. Run **Release prep** from the Actions tab to open a release pull request for whatever is in Unreleased. It refuses while the newest published release is not recorded on `main`. When `pyproject.toml` on `main` holds a new version with a dated CHANGELOG section, `release-tag.yml` pushes the tag, which runs `release.yml`.

The workflows act through a GitHub App, because pushes, merges and tags made with `GITHUB_TOKEN` start no further workflows. Its client ID and private key are stored as `RELEASE_APP_CLIENT_ID` and `RELEASE_APP_PRIVATE_KEY` Actions secrets.

A beta is a tag such as `v1.2.0-beta.1`, with `version = "1.2.0b1"` in `pyproject.toml` and a CHANGELOG heading `[1.2.0-beta.1]`. It publishes the image tags `1.2.0-beta.1` and `beta`, never `latest`, and a GitHub release marked as prerelease.
