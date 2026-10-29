# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] - 2026-10-29

### Changed

- Update yt-dlp to 2026.10.26.

## [0.1.1] - 2026-10-24

### Changed

- Let landing page visitors pick Plex or Jellyfin and Docker Compose, docker run or Unraid, and show the matching install steps.
- Drop the manual Unraid template steps now that Tellyard is in Unraid Apps, and name the Docker Hub image on the landing page.
- Read every stream through Tellyard, so ffmpeg reaches only addresses Tellyard has checked.
- Report on `/healthz` when the guide cannot be saved to `/data`, and keep serving the guide that was built.
- Use less CPU and memory while streaming: all HTTPS requests share one TLS setup instead of loading every CA certificate again, and stream data is read in larger blocks.
- Ship the Python standard library compiled, and let the container health check load only what it needs. Idle CPU and start-up time go down.
- Build ffmpeg from source with only the parts Tellyard uses, under the LGPL instead of the GPL. The image is about a third smaller.
- Update yt-dlp to 2026.10.21.

### Removed

- ffprobe from the image, and the undocumented `FFPROBE` setting. `tellyard check` reads the resolution from ffmpeg. The image is about a quarter smaller.

### Fixed

- Keep real shows in the guide when a guide source cannot be downloaded or has no current shows: use the last good copy of that source, and download only that source again after 15 minutes instead of at the next scheduled rebuild.
- Keep building the guide when a source has a show time out of range or a damaged gzip file.
- Name Jellyfin next to Plex in `tellyard --help`.
- Free the tuner within about a second when the viewer leaves. ffmpeg could hold it for up to 5 seconds.

### Security

- Refuse private and local addresses unless the channel, guide source or `HEARTBEAT_URL` names that host, also after a redirect and inside playlists.
- Refuse redirects to addresses other than http and https.
- Limit every download, including guide sources, to a total time.
- Bound the memory a guide source can use, including compressed sources, and refuse sources that declare their own XML entities.
- Send `Authorization` and `Cookie` headers only to the hosts the channel names.
- Show only the host of an address in logs and error messages.
- Limit the server to 64 connections and each request to 10 seconds and 128 KB.
- Update certifi to 2026.10.23, which removes the emSign root certificates with compromised intermediate keys (GHSA-6q48-6c7w-9gw2).
- Update the system CA certificates to Debian 20250419+deb13u1, which removes the same emSign root for Tellyard's own requests (DSA-6572-1).
- Update Brotli to 1.2.1, which fixes an out-of-bounds write when yt-dlp decodes a crafted response (CVE-2026-111497).

## [0.1.0] - 2026-10-17

### Added

- Add an Unraid template, `templates/tellyard.xml`, and Unraid install steps in the README.
- Publish the image to Docker Hub as `sandobserver/tellyard` as well as to ghcr.io.
- Add the `live` source for channels on YouTube, Twitch, Kick and other sites yt-dlp supports. The `youtube` key still works.
- Add `HEARTBEAT_URL`, called once a minute while Tellyard is healthy.
- Add `/healthz`, which answers 503 with a reason when the guide has stopped updating. `/health` stays as an alias.

### Changed

- Point the install steps at the `latest` image tag.
- Start with no channels when `channels.toml` is missing, and load it once it appears. `/healthz` reports the missing file.
- Report a channel that is not live without naming YouTube, since `live` covers other sites.
- Print a settings summary at start and write log lines as logfmt with UTC times.
- Run the landing page guide on the visitor's clock, with shows that start at different times.

### Fixed

- Use `info` when `LOG_LEVEL` is set but empty.
- Fix screen reader labels on the landing page and send a Content Security Policy with it.
- Free the tuner within a second when the viewer leaves while a channel is still tuning, frozen or reconnecting.
- Scan broadcaster pages and master playlists in linear time, so a hostile page cannot stall every stream.
- Remove credentials, path and query of stream addresses from ffmpeg messages in the log.
- Pick the default audio track when it is carried in the video stream.
- Accept only http and https addresses from master playlists.
- Stop a page, playlist or token request that takes longer than 30 seconds.
- Report a `channels.toml` that is not UTF-8 as a channel file error, and keep the guide thread running after an unexpected error.
- Answer a tune that fails on an unexpected playlist value with the usual stream error.
- Explain that a subchannel number needs quotes in `channels.toml`, and ignore spaces around a page `match`.

## [0.1.0-beta.2] - 2026-10-13

### Added

- License Tellyard under Apache 2.0, with a NOTICE for the name and the software in the image.
- Add a Legal section to the README.
- Add documentation pages for channels, Plex, how it works, troubleshooting and maintenance.
- Add a security policy, contributing guide and issue templates.
- Publish the image for 64-bit ARM as well as Intel and AMD.
- Add the Tellyard logo and a landing page at tellyard.sandobserver.com.

### Changed

- Point the install steps at the `beta` image tag until the first stable release.

### Removed

- Remove the start-up cleanup of artwork files left by early test builds.

### Fixed

- End a show when the next one starts, so overlapping listings from a guide source no longer collide.

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
