# Troubleshooting

[Back to the README](../README.md)

Read the log first: `docker logs --tail 100 tellyard`. At start, Tellyard prints a short summary of its settings. After that, each line has the UTC time, a level (`DBG`, `INF`, `WRN` or `ERR`), the message, and details as `key=value`:

```text
2026-10-12T21:04:17.512Z WRN msg="stream failed to start" channel=101 title="Example News" reason="the channel is not live right now (was_live)"
```

Set `LOG_LEVEL=debug` to see every request Plex makes. `docker exec tellyard tellyard check 101` tunes one channel and prints what went wrong.

## Log messages

| Message | What to do |
| --- | --- |
| `stream failed to start`, reason `the channel is not live right now` | The channel has no live stream at the moment. Open its page in a browser. If a YouTube channel runs several streams, set `match` to part of the right stream's title. |
| `stream failed to start`, reason `yt-dlp returned no HLS playlist for the stream` | The site changed how it serves the stream. Update to the latest Tellyard release, which carries the latest yt-dlp. |
| `stream failed to start`, reason with a yt-dlp error, such as `Sign in to confirm you're not a bot` | The site refused the request from your server's address. Update to the latest release and try again later. |
| `stream failed to start`, reason `yt-dlp took longer than 60 s` | The site or your connection is slow. Tune in again. |
| `stream failed to start`, reason `no playlist URL on the channel page` | The page no longer contains an `.m3u8` address, or none containing `match`. Find the playlist again, see [Finding sources](channels.md#finding-sources). |
| `stream failed to start`, reason `could not load the playlist:` with `HTTP 403` | The playlist refuses the request. It may need a `Referer` header (`headers`), or a fresh signed address (`token`). |
| `stream failed to start` or `source request failed`, reason `refused <address>: private addresses need a host named in the configuration` | The stream, page or a redirect points at a private address that the channel does not name. Write that host in `hls` or `page`, see [Addresses Tellyard reaches](how-it-works.md#addresses-tellyard-reaches). |
| `source request failed` | A playlist, key or video segment could not be loaded. Now and then is normal. If the stream does not start, see `reason`. |
| `stream failed to start`, reason `the stream URL did not return an HLS playlist` | The `hls` address is not a playlist, or the server answered with an error page. |
| `stream failed to start`, reason `no video arrived within 30 s` | The source answered but sent no video. Check the channel with `tellyard check`. |
| `no data, restarting` | The source stalled and Tellyard restarted it. Now and then is normal. Often means the source is unreliable. |
| `stream given up` | The source failed 3 times in a row. Plex shows an error. Tune in again later. |
| `all tuners are busy` | More streams were asked for than `TUNERS` allows. Raise `TUNERS`, or stop a stream. |
| `no channel file yet, add your channels to it` | Tellyard found no `channels.toml` at `file`. Put your channel list there. Tellyard loads it within a minute. |
| `channel file has an error, keeping the previous channels` | Fix the mistake named in `reason`. `tellyard validate` checks the file. |
| `guide source failed, using placeholders` | The XMLTV address could not be downloaded or read, or is too large. See `reason`, and open the address in a browser. `copy` says why the last good copy was not used: `none`, `no current listings`, or the error reading it. |
| `guide source failed, using the last good copy` | As above, or the source has no current shows (`reason="no current listings"`). Tellyard uses its copy from `saved_at`. A failed download is tried again in 15 minutes. |
| `guide source copy not saved` | Tellyard cannot write to `/data/feeds`. Give user 1000 write access to the data folder. |
| `guide not saved` | Tellyard cannot write to `/data`. `/healthz` reports it. Give user 1000 write access to the data folder, such as `sudo chown 1000:1000 ./data`. |
| `too many connections, refusing new ones` | More than 64 connections are open. Something on your network keeps connections open. |
| `guide source has no current listings` | The source has no shows for the listed `ids` right now. Check each `id` against the `<channel id="...">` lines in the file. |
| `HEARTBEAT_URL call failed` | The monitor did not answer. Check the address in `HEARTBEAT_URL`. |

## In Plex

| Problem | What to do |
| --- | --- |
| Plex does not find the tuner | Expected. Enter Tellyard's address by hand, such as `192.168.1.10:5004`. |
| A channel plays in Plex Web but not in the iPhone, iPad or Apple TV app ("Can't Reach Server") | Turn on **Force Direct Play** in the app, under **Settings > Video & Audio**. See [Plex apps](plex.md#plex-apps). |
| The guide still shows a removed channel, or misses a new one | In Plex, **Settings > Live TV & DVR**, open the DVR, and choose **Refresh Guide**. |
| A show's detail page is empty in the iPhone, iPad or Apple TV app | A Plex app problem with XMLTV guides. Plex Web shows the details. |
| `tellyard check` reports `below the 1080p minimum` | The source sends nothing higher. Remove the channel, or set `min_height` for it in `channels.toml`. |
