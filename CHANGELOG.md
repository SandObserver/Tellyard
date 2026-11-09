# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Bring the landing page guide in with one short entrance and speed up its menus, install steps and press feedback.

### Fixed

- Stop the landing page from scrolling sideways on 320 pixel screens and with long translated text.
- Show the full keyboard focus ring on landing page code boxes and picker menus.
- Keep gentle fades for reduced motion, follow the increased contrast setting and say when copying is blocked.

## [0.2.0] - 2026-11-09

### Added

- Add `allow` for a channel: other `host:port` addresses on your home network that its stream uses.

### Changed

- Refuse proxy options in `YTDLP_ARGS`. yt-dlp now uses `http_proxy` or `https_proxy` through Tellyard, and no longer `all_proxy`.
- Stop yt-dlp from loading its config files and plugins. Set yt-dlp options in `YTDLP_ARGS`.
- Stop every stream when Tellyard stops, and wait up to 8 seconds for them to end.

### Fixed

- Treat a response that ends before its declared length as a failed request. A cut playlist was used as if it were whole.
- Ignore the body of a redirect. A large one could use all of Tellyard's memory.
- Try the other proxy when `http_proxy` and `https_proxy` differ and the first one refuses a connection.
- Keep every address of channels with a separate audio track and long playlists. The audio addresses pushed out the first video segments.
- Use the new title of a last show without an end time. The last good copy kept the old one.
- Stop downloading a guide source that is too large or unreadable on every retry when it has no saved copy.
- Refuse guide sources that declare XML entities in any encoding. One in UTF-16 could use more memory than the container has.
- Refuse playlists with an address longer than 16 KB. Long addresses could fill memory during a stream.
- Clean long yt-dlp error lines for the log without delay. One such line could freeze every stream for minutes.
- Accept a proxy setting without `http://`, such as `http_proxy=192.168.1.5:3128`. Every request was refused.
- Free the tuner within a second when the viewer leaves during a page, token or playlist request. It stayed taken for up to 30 seconds.
- Free the tuner within a second when the viewer leaves while Tellyard connects to a source that does not answer. It stayed taken for 10 seconds per address of the source.
- See a viewer leave who sent anything after the request. The lookup and reconnects ran on for nobody.
- Stop yt-dlp lookups and keep ffmpeg away from Ctrl-C when Tellyard stops outside Docker. A lookup kept running, and Ctrl-C started a new one.
- Clean up the landing page guide, its animations and the install section. The time line keeps one thickness, the guide no longer jumps every half hour, and the guide fits at every screen width.
- Keep sound in sync on channels whose audio is a separate track. It could run seconds ahead of or behind the picture.
- Reconnect when the separate audio track of a channel fails. The picture played on without sound.
- Play streams whose segment addresses have no file extension, such as `/seg/5`. They never started.
- Give up after three tries on a source that keeps freezing after a few seconds of video. It was retried forever.
- Report a failed ffmpeg start during a reconnect and try again. The stream ended without a log line.
- Report in `tellyard check` the height of the video that plays. A source with several video streams reported the tallest one.
- Skip a variant with a malformed address. The whole channel failed.
- Refuse playlists that list more than 1024 addresses. Reading one took up to 16 seconds per request.
- Keep the guide of every channel when one guide source uses a multi-byte encoding such as Shift_JIS or GBK. The whole guide stopped updating.
- Read guide shows without an end time. They end when the next show starts. Such a source gave only placeholder blocks.
- Stop the last good copy of a guide source from bringing back shows that the source moved or dropped.
- Stop the last good copy of a guide source from growing with every retry.
- Keep the last good copy of a guide source whole when the source has a comment after its end.
- Rebuild the guide 15 minutes after a failed rebuild that followed a change to `channels.toml`. New channels were missing until the next scheduled rebuild.
- Log `guide source has no current listings` for an id the source dropped, also when the last good copy still has shows for it.
- Call `HEARTBEAT_URL` once a minute also while the guide downloads. A slow guide source delayed it by up to 10 minutes per source.

### Security

- Send `live` channel lookups through a proxy inside Tellyard. yt-dlp followed redirects and page links to private addresses the channel does not name.
- Stop trusting the host of every proxy setting for direct requests. A proxy only for https, `all_proxy` or `no_proxy` made that host reachable on every port.
- Check every address in a playlist that a source lists twice, once as a key and once as a playlist. ffmpeg fetched the addresses inside it directly, without the private-address rules.
- Refuse private addresses written as other numbers behind a proxy, such as `2130706433` or `127.1`.
- Reach a named private address only on its own port, and send `Authorization` and `Cookie` only to the scheme, host and port written in the channel.
- Refuse IPv4 addresses inside IPv6 addresses, the container's own IPv6 networks and Azure's metadata address.
- Stop a `live` channel lookup whose yt-dlp output passes 16 MB. A large answer could use up the container's memory.

## [0.1.3] - 2026-11-01

### Changed

- Stop yt-dlp from sending a made-up address in another country to sites that limit streams by region. A `live` channel limited to another region now fails with the site's region error.
- Start guide placeholder blocks at local midnight in `TZ` instead of UTC. A `TZ` that is not a known time zone name logs a warning and keeps UTC.

### Fixed

- Report the error yt-dlp gives when a stream cannot be opened, such as a region limit, instead of its last hint line.
- Read guide times that name their time zone as `UTC` or `GMT`. Log the entries whose times cannot be read.
- Keep building the guide when a guide source lists a show that ends far in the future. All channels lost their guide.
- Rebuild the guide after the system clock is set back. It stopped updating until the clock caught up.
- Read guide times that have no space before the time zone offset. They were read as UTC.
- Read guide times without seconds or minutes, such as `202610111530 +0300`. They were read at the wrong time.
- Log the time the last good copy of a guide source was downloaded, also after a retry.
- Play streams and pass the container health check when `http_proxy` is set. Both sent requests meant for Tellyard itself to the proxy.
- Refuse a guide source with an entry nested more than 8 levels deep. A few such entries made the guide hundreds of megabytes.
- Play HLS streams whose master playlist starts with a blank line.
- Keep the later shows of the last good copy of a guide source when a new download ends earlier or lacks a channel. Those hours showed placeholder blocks until the next rebuild.
- Log why the last good copy of a guide source was not used when a channel falls back to placeholder blocks.
- Keep the last good copy of a guide source readable when it holds shows with long text in a non-Latin script. Those channels showed placeholder blocks when the source failed.
- Keep updating the guide when a guide source names a text encoding Python does not know. The guide stopped updating for all channels.
- Keep the shows after the guide window in the last good copy of a guide source. While a source stayed shorter than its copy, the later shows ran out after one rebuild.
- Rebuild the guide when `channels.toml` changes while the guide is being built. New or renamed channels stayed out of the guide until the next scheduled rebuild.
- Stop the Deno helper when a channel lookup is cancelled or takes too long. It kept running.
- Clean up finished helper processes when the container runs without `--init`. The image now starts with tini.
- Stop yt-dlp and Deno when `tellyard check` or `tellyard serve` is stopped with Ctrl-C during a channel lookup.

### Security

- Update Python to 3.13.18, which rejects the compromised eMudhra intermediate certificates in every TLS connection (CVE-2026-115557).

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
