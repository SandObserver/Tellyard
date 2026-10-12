# Troubleshooting

[Back to the README](../README.md)

Read the log first: `docker logs --tail 100 tellyard`. Each line has the time, a level, the part of Tellyard that wrote it, and the message:

```text
2026-10-12 21:04:17,512 WARNING tellyard.server: 101 Example News: cannot start: the YouTube channel is not live right now (was_live)
```

Set `LOG_LEVEL=debug` to see every request Plex makes. `docker exec tellyard tellyard check 101` tunes one channel and prints what went wrong.

## Log messages

| Message | What to do |
| --- | --- |
| `cannot start: the YouTube channel is not live right now` | The channel has no live stream at the moment. Open its `/live` page in a browser. If it runs several streams, set `match` to part of the right stream's title. |
| `cannot start: YouTube returned no HLS playlist for the stream` | YouTube changed how it serves the stream. Update to the latest Tellyard release, which carries the latest yt-dlp. |
| `cannot start:` with a yt-dlp error, such as `Sign in to confirm you're not a bot` | YouTube refused the request from your server's address. Update to the latest release and try again later. |
| `cannot start: yt-dlp took longer than 60 s` | YouTube or your connection is slow. Tune in again. |
| `cannot start: no playlist URL on the channel page` | The page no longer contains an `.m3u8` address, or none containing `match`. Find the playlist again, see [Finding sources](channels.md#finding-sources). |
| `cannot start: could not load the playlist:` with `HTTP 403` | The playlist refuses the request. It may need a `Referer` header (`headers`), or a fresh signed address (`token`). |
| `cannot start: the stream URL did not return an HLS playlist` | The `hls` address is not a playlist, or the server answered with an error page. |
| `cannot start: no video arrived within 30 s` | The source answered but sent no video. Check the channel with `tellyard check`. |
| `no data for 20 s, restarting` | The source stalled and Tellyard restarted it. Now and then is normal. Often means the source is unreliable. |
| `giving up after 3 failed attempts` | The source keeps failing. Plex shows an error. Tune in again later. |
| `all 4 tuners are busy` | More streams were asked for than `TUNERS` allows. Raise `TUNERS`, or stop a stream. |
| `channels.toml changed but has an error, keeping the previous channels` | Fix the mistake named in the message. `tellyard validate` checks the file. |
| `guide source ... failed, using placeholders` | The XMLTV address could not be downloaded or read. Open it in a browser. |
| `guide source ... has no current listings for ...` | The source has no shows for that `id` right now. Check the `id` against the `<channel id="...">` lines in the file. |

## In Plex

| Problem | What to do |
| --- | --- |
| Plex does not find the tuner | Expected. Enter Tellyard's address by hand, such as `192.168.1.10:5004`. |
| A channel plays in Plex Web but not in the iPhone, iPad or Apple TV app ("Can't Reach Server") | Turn on **Force Direct Play** in the app, under **Settings > Video & Audio**. See [Plex apps](plex.md#plex-apps). |
| The guide still shows a removed channel, or misses a new one | In Plex, **Settings > Live TV & DVR**, open the DVR, and choose **Refresh Guide**. |
| A show's detail page is empty in the iPhone, iPad or Apple TV app | A Plex app problem with XMLTV guides. Plex Web shows the details. |
| `tellyard check` reports `below the 1080p minimum` | The source sends nothing higher. Remove the channel, or set `min_height` for it in `channels.toml`. |
