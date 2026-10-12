# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- License Tellyard under Apache 2.0, with a NOTICE for the name and the software in the image.
- Add a Legal section to the README.
- Add documentation pages for channels, Plex, how it works, troubleshooting and maintenance.
- Add a security policy, contributing guide and issue templates.
- Publish the image for 64-bit ARM as well as Intel and AMD.

## [0.1.0-beta.1] - 2026-10-11

First version.

- Presents itself to Plex as an HDHomeRun tuner and lists the channels from `channels.toml`.
- Resolves YouTube live channels, broadcaster pages and HLS playlists when a viewer tunes in,
  picks the best quality, and remuxes it with ffmpeg without re-encoding.
- Reconnects on its own when a source drops or stalls.
- Builds an XMLTV guide from XMLTV sources, with placeholder blocks for every gap.
- Reloads `channels.toml` on change and keeps the last good list if the new one has a mistake.
- `tellyard check` reports the resolution each channel really sends, against an optional `MIN_HEIGHT`.
- Takes its settings from environment variables and its channels from `channels.toml`.
