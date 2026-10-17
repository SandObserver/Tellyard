# Channels

[Back to the README](../README.md)

Channels live in one file, `config/channels.toml`. [`channels.example.toml`](../channels.example.toml) has one channel of each source type to start from.

## A channel

```toml
[[channel]]
number = 101
name = "Example News"
logo = "https://example.com/logos/example-news.png"
live = "https://www.youtube.com/@example/live"
[channel.guide]
category = "News"
```

| Key | What it does |
| --- | --- |
| `number` | Channel number, such as `101`, or `"101.1"` in quotes for a subchannel. Each number once. |
| `name` | Name shown in Plex and the guide. |
| `logo` | Public https link to the channel logo. Plex loads it as given. |
| `live` | A live channel on YouTube, Twitch, Kick or another site yt-dlp supports, such as `https://www.youtube.com/@example/live` or `https://www.twitch.tv/example`. `youtube` is the older name for this key and still works. |
| `page` | A web page that contains the stream's `.m3u8` address. |
| `hls` | The stream's `.m3u8` playlist address. |
| `match` | For `live` on YouTube: part of the title of the live stream to play, when the channel runs several. For `page`: part of the `.m3u8` address to pick. |
| `headers` | For `page` and `hls`: extra request headers, such as `{ Referer = "https://example.com/" }`. |
| `token` | For `hls`: a token service that returns a fresh signed address before every tune. `{url}` in it becomes the `hls` address. The reply must be JSON. |
| `token_field` | The JSON key holding the signed address. Default `url`. |
| `min_height` | Lowest resolution `tellyard check` accepts for this channel, such as `720`. Overrides `MIN_HEIGHT`. |

Set exactly one of `live`, `page` or `hls`.

## The guide

```toml
[guide_sources]
my-guide = "https://example.com/guide.xml.gz"

[[channel]]
# ...
[channel.guide]
xmltv = "my-guide"
id = "ExampleNews.us"
category = "News"
```

| Key | What it does |
| --- | --- |
| `xmltv` | A name from `[guide_sources]`, or an XMLTV address. Plain and gzipped files both work. |
| `id` | The channel's id inside that XMLTV file. |
| `title` | Title of the placeholder blocks. Default: the channel name. |
| `description` | Description of the placeholder blocks. |
| `category` | Category of the placeholder blocks, such as `News`. |
| `hours` | Length of each placeholder block, from `0.5` to `24`. Must divide a day evenly, such as `1`, `2`, `3`, `4`, `6` or `12`. Default `2`. |

A channel without `xmltv` gets placeholder blocks only.

## Finding sources

- **YouTube:** open the channel's page and add `/live` to its address. If the channel runs several live streams at once, add `match` with part of the stream's title.
- **Twitch and Kick:** use the channel's address, such as `https://www.twitch.tv/example`. Twitch shows a 30-second "Commercial break in progress" screen each time you tune in.
- **A broadcaster's own stream:** many broadcasters publish their live channel as an HLS playlist (`.m3u8`). Use `hls` with the playlist address, or `page` with the address of the page that links to it. Some streams need a `Referer` header, set with `headers`.
- **Guides:** any XMLTV file or address works. Find your channel in the file and copy the `id` from its `<channel id="...">` line.
- **Logos:** a public https link to a wide, transparent PNG, the largest official version you can find.

Only add streams you are allowed to watch. Run `docker exec tellyard tellyard check` after adding a channel to confirm it plays at the quality you expect.

## Changing the list

Edit the file while Tellyard runs. It reloads within a minute and keeps the previous channels if the new file has a mistake. `docker exec tellyard tellyard validate` checks the file and lists the channels it found. After adding or removing channels, refresh the guide in Plex, see [Plex](plex.md#after-changing-channels).
