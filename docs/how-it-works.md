# How it works

[Back to the README](../README.md)

## Tuning in

Plex sees Tellyard as an HDHomeRun network tuner with `TUNERS` tuners. When a viewer picks a channel, Plex asks Tellyard for `/stream/<number>`, and Tellyard:

1. Finds the channel's current stream:
   - `live`: asks yt-dlp for the channel's live stream. On YouTube, with `match`, it first lists the channel's live streams and takes the one whose title contains the text. If none does, it uses the `/live` page.
   - `page`: loads the page and takes the first `.m3u8` address in it, or the first one containing `match`.
   - `hls`: uses the playlist address. With `token`, it first asks the token service for a signed address.
2. Reads the playlist and picks the highest resolution, with its default audio track.
3. Starts ffmpeg, which copies the video and audio into one MPEG-TS stream without re-encoding.
4. Waits up to 30 seconds for the first video, then hands the stream to Plex.

The stream address is found again on every tune, so addresses that expire never break playback. When the viewer leaves, ffmpeg stops and the tuner is free, also while Tellyard is still finding the stream or reconnecting. Nothing runs while nobody watches.

## When a source drops

- If no data arrives for 20 seconds, Tellyard restarts the stream.
- If the source ends, Tellyard finds the stream again and reconnects.
- If it keeps failing within 30 seconds of each start, Tellyard tries three times, waiting 2 and then 4 seconds between tries, and then gives up. Plex then shows an error, and tuning in again starts over.

During a reconnect the picture freezes. In the worst case, a YouTube channel can take over a minute to come back.

## The guide

Tellyard publishes an XMLTV guide at `/guide.xml` for `GUIDE_DAYS` days, starting two hours back.

- A channel with `[channel.guide] xmltv` and `id` gets its shows from that XMLTV source. Each source is downloaded once per rebuild, however many channels use it.
- If two shows in a source overlap, the earlier one ends when the next one starts.
- Every gap, and every channel without a guide, gets placeholder blocks of `hours` length, aligned to the clock. Their title is the channel name unless `title` says otherwise.
- Every entry gets a category, and every entry that is not a movie gets an episode number with its air date, so Plex files it as a TV episode.
- The channel logo is the `logo` URL as written. Shows keep the pictures their source guide gives them; placeholder blocks have none.

The guide is rebuilt every `GUIDE_REFRESH_HOURS` hours and within a minute of a change to `channels.toml`. If a rebuild fails, Tellyard keeps the last guide and tries again 15 minutes later. If a guide source fails, its channels get placeholder blocks until the next rebuild.

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
| `/healthz` | Status as JSON. Answers 503 with a `reason` when there are no channels or the guide has stopped updating. Also at `/health`. |

Tellyard does not answer HDHomeRun network discovery, so Plex never finds it on its own. Enter its address in Plex by hand.

## What it stores

`/data` holds the last guide, `guide.xml`, and the caches of yt-dlp and Deno under `cache/`. All of it is rebuilt when missing. `/config/channels.toml` is only read.
