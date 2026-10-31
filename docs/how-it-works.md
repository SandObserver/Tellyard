# How it works

[Back to the README](../README.md)

## Tuning in

Plex sees Tellyard as an HDHomeRun network tuner with `TUNERS` tuners. When a viewer picks a channel, Plex asks Tellyard for `/stream/<number>`, and Tellyard:

1. Finds the channel's current stream:
   - `live`: asks yt-dlp for the channel's live stream. On YouTube, with `match`, it first lists the channel's live streams and takes the one whose title contains the text. If none does, it uses the `/live` page.
   - `page`: loads the page and takes the first `.m3u8` address in it, or the first one containing `match`.
   - `hls`: uses the playlist address. With `token`, it first asks the token service for a signed address.
2. Reads the playlist and picks the highest resolution, with its default audio track.
3. Starts ffmpeg, which copies the video and audio into one MPEG-TS stream without re-encoding. ffmpeg reads the stream only through Tellyard, so every address it uses passes the checks in [Addresses Tellyard reaches](#addresses-tellyard-reaches).
4. Waits up to 30 seconds for the first video, then hands the stream to Plex.

The stream address is found again on every tune, so addresses that expire never break playback. When the viewer leaves, ffmpeg stops and the tuner is free, also while Tellyard is still finding the stream or reconnecting. Nothing runs while nobody watches.

## When a source drops

- If no data arrives for 20 seconds, Tellyard restarts the stream.
- If the source ends, Tellyard finds the stream again and reconnects.
- If it keeps failing within 30 seconds of each start, Tellyard tries three times, waiting 2 and then 4 seconds between tries, and then gives up. Plex then shows an error, and tuning in again starts over.

During a reconnect the picture freezes. In the worst case, a YouTube channel can take over a minute to come back.

## Addresses Tellyard reaches

Tellyard fetches every page, playlist, key and video segment itself. ffmpeg receives them from Tellyard over a private address inside the container.

- Only `http` and `https` addresses are used, also after a redirect.
- An address on your home network, on the host itself or in another private range is reached only when its host is written in that channel's `live`, `page`, `hls` or `token`, is a guide source, or is `HEARTBEAT_URL`. A stream, page or redirect that points at any other private address fails. The log then says `refused <address>: private addresses need a host named in the configuration`.
- `Authorization` and `Cookie` headers go only to the hosts written in the channel. Other `headers` go to every host the stream uses.
- Each request has a time limit: 30 seconds for a page, playlist or token answer when a stream starts, 15 seconds for a playlist during playback, 60 seconds for a video segment and 10 minutes for a guide download.
- With `http_proxy` or `https_proxy` set, requests go through the proxy. Tellyard then refuses private addresses written as numbers, such as `192.168.1.1`. A name that points to a private address is up to the proxy. Requests inside the container, from ffmpeg to Tellyard and from the health check, skip the proxy.

yt-dlp opens `live` channels on its own.

## The guide

Tellyard publishes an XMLTV guide at `/guide.xml` for `GUIDE_DAYS` days, starting two hours back.

- A channel with `[channel.guide] xmltv` and `id` gets its shows from that XMLTV source. Each source is downloaded once per rebuild, however many channels use it.
- If two shows in a source overlap, the earlier one ends when the next one starts.
- Every gap, and every channel without a guide, gets placeholder blocks of `hours` length, aligned to the clock in `TZ`, or UTC when it is not set or not a known time zone name. On a daylight saving change, the blocks around the change can be longer or shorter than the rest. Their title is the channel name unless `title` says otherwise.
- Every entry gets a category, and every entry that is not a movie gets an episode number with its air date, so Plex files it as a TV episode.
- The channel logo is the `logo` URL as written. Shows keep the pictures their source guide gives them; placeholder blocks have none.

The guide is rebuilt every `GUIDE_REFRESH_HOURS` hours and within a minute of a change to `channels.toml`. If a rebuild fails, Tellyard keeps the last guide and tries again 15 minutes later.

Tellyard keeps the last good copy of each guide source in `/data/feeds`. When a source fails, its channels use that copy, or placeholder blocks when there is none. A source fails when it cannot be downloaded, is not valid XMLTV, is larger than 256 MB to download or 1 GB unpacked, declares its own XML entities, has an entry larger than 64 KB or nested more than 8 levels deep, or has more than 32 MB of shows for your channels. A source with no current shows for any of your channels also keeps the last good copy. When a new download ends earlier for a channel than the last good copy, or lacks the channel, the later shows come from the copy and are saved with the new download. A show that started before the guide window is not taken from the copy.

After a failed download or unreadable file, Tellyard downloads only that source again 15 minutes later, and keeps doing so until it works. The other sources are downloaded at the next scheduled rebuild. A source that is too large or declares entities is not tried again before the next scheduled rebuild.

Tellyard serves no images itself. Tools such as [Posteryard](https://github.com/SandObserver/Posteryard) can add posters to the Plex guide.

## The channel list

Tellyard checks `channels.toml` for changes every time Plex asks for the line-up or a stream, and once a minute. A file with a mistake is ignored, with an error in the log, and the previous channels stay.

## HTTP endpoints

| Path | Use |
| --- | --- |
| `/discover.json`, `/lineup.json`, `/lineup_status.json`, `/device.xml` | The HDHomeRun API Plex reads. |
| `/stream/<number>` | One channel as MPEG-TS. Also at `/auto/v<number>`. |
| `/guide.xml` | The XMLTV guide. Also at `/epg.xml`. |
| `/playlist.m3u` | An M3U playlist for players such as VLC. |
| `/healthz` | Status as JSON. Answers 503 with a `reason` when there are no channels, the guide has stopped updating, or the guide cannot be saved to `/data`. Also at `/health`. |

Tellyard handles up to 64 connections at once and closes any more. A request must arrive complete within 10 seconds and be smaller than 128 KB.

Tellyard does not answer HDHomeRun network discovery, so Plex never finds it on its own. Enter its address in Plex by hand.

## What it stores

`/data` holds the last guide, `guide.xml`, the last good copy of each guide source under `feeds/` (up to 256 MB each), and the caches of yt-dlp and Deno under `cache/`. All of it is rebuilt when missing. `/config/channels.toml` is only read.
