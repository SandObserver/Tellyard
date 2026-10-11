# Tellyard

Live channels for Plex, tuned only while someone is watching.

Tellyard presents itself to Plex as an HDHomeRun network tuner. Channels are listed in one
`channels.toml` file. When a viewer tunes in, Tellyard finds the channel's current stream,
picks its best quality, and hands it to Plex through ffmpeg without re-encoding. When the
viewer leaves, ffmpeg stops. Nothing is downloaded while nobody watches.

- **Sources:** YouTube live channels (through yt-dlp, resolved fresh on every tune, so expiring
  links never break playback), broadcaster web pages that embed an HLS playlist, and direct HLS
  playlists.
- **Guide:** an XMLTV guide that merges real schedules from any XMLTV source with clean
  placeholder blocks, so every channel always shows a correct name, logo and listing. Every
  entry is filed as a TV episode with a category. Channel logos are the URLs from
  `channels.toml`, and shows keep the pictures their source guide gives them.
- **Reliability:** if a source drops mid-show, Tellyard reconnects on its own. A stalled source is
  restarted after 20 seconds.
- **Quality check:** `tellyard check` tunes each channel and reports the resolution it really
  sends, so a channel below your minimum never slips in.

## Getting started

You need [Docker](https://docs.docker.com/get-started/get-docker/) and a Plex Media Server.
Watching live TV is free; recording needs Plex Pass.

### 1. Write your channel list

Create a `config` folder and copy [`channels.example.toml`](channels.example.toml) into it as
`channels.toml`. Replace the examples with your channels. See [Channels](#channels) and
[Finding sources](#finding-sources).

### 2. Create the compose file

Save this as `compose.yml` next to the `config` folder.

```yaml
services:
  tellyard:
    image: ghcr.io/sandobserver/tellyard:latest
    container_name: tellyard
    restart: unless-stopped
    init: true
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    mem_limit: 512m
    environment:
      # Time zone for log timestamps
      - TZ=America/New_York
      # Streams that may play at the same time
      - TUNERS=4
      # Lowest resolution `tellyard check` accepts: off, or a height such as 1080
      - MIN_HEIGHT=off
    volumes:
      - ./config:/config:ro
      - ./data:/data
    ports:
      - "5004:5004"
```

`./data` is where Tellyard caches the guide and the yt-dlp files between restarts.

### 3. Start it

In the folder with `compose.yml`:

```sh
mkdir -p ./data && sudo chown 1000:1000 ./data
docker compose up -d
```

Tellyard runs as user 1000 and writes only to `./data` and `/tmp`.

<details>
<summary>Without Compose</summary>

```sh
mkdir -p ./data && sudo chown 1000:1000 ./data
docker run -d --name tellyard --restart unless-stopped \
  --init --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true --memory 512m \
  -e TZ=America/New_York -e TUNERS=4 -e MIN_HEIGHT=off \
  -v ./config:/config:ro -v ./data:/data -p 5004:5004 \
  ghcr.io/sandobserver/tellyard:latest
```

</details>

### 4. Check your channels

```sh
docker exec tellyard tellyard check
```

This tunes each channel once and reports the resolution it really sends. Fix or remove any
channel that fails before adding Tellyard to Plex.

### 5. Add it to Plex

In Plex: **Settings > Live TV & DVR > Set up Plex DVR**. If Plex does not find the tuner, enter
its address by hand, for example `192.168.1.10:5004`. For the guide, choose
**Have an XMLTV guide on your server? Click here to use it** and enter
`http://192.168.1.10:5004/guide.xml`.

## Channels

Each channel in `channels.toml` needs a number, a name, a logo and one source:

```toml
[[channel]]
number = 101
name = "Example News"
logo = "https://example.com/logos/example-news.png"
youtube = "https://www.youtube.com/@example/live"
[channel.guide]
category = "News"
```

[`channels.example.toml`](channels.example.toml) lists every option, with one channel of each
source type.

Tellyard reloads the file within seconds of a change and keeps the previous channels if the new
file has a mistake. `docker exec tellyard tellyard validate` reads the file and lists the
channels it found.

After adding or removing channels, refresh the guide in Plex so its channel list matches.

## Finding sources

- **YouTube:** open the channel's page and add `/live` to its address, such as
  `https://www.youtube.com/@example/live`. If the channel runs several live streams at once, add
  `match` with part of the stream's title.
- **A broadcaster's web player:** open the live page in a browser, open the developer tools,
  and filter the **Network** tab for `m3u8`. If the playlist URL appears in the page itself, use
  `page` with the page address. Otherwise use `hls` with the playlist URL. Some players need a
  `Referer` header, set with `headers`.
- **Guides:** any XMLTV file works. [i.mjh.nz](https://i.mjh.nz) publishes guides for Plex,
  Pluto TV, Samsung TV Plus and Roku channels, and [epgshare01](https://epgshare01.online)
  publishes guides by country. Open the file, find your channel, and copy the `id` from its
  `<channel id="...">` line. A channel without a guide gets placeholder blocks.
- **Logos:** a public https link to a wide, transparent PNG. Plex's own channel logos, linked
  in the i.mjh.nz Plex guide, work well.

Run `tellyard check` after adding a channel to confirm it plays at the quality you expect.

## Commands

Run a command inside the container:

```sh
docker exec tellyard tellyard check
```

| Command | What it does |
| --- | --- |
| `serve` | Run the tuner (the container default). |
| `validate` | Read `channels.toml` and list the channels. |
| `check [NUMBER...]` | Tune each channel and report the resolution it sends. Exits 1 if any fails. |
| `guide [-o FILE]` | Build the guide once. |
| `health` | Exit 0 when the running service answers. Used by the container health check. |

## Settings

Set these as environment variables.

| Setting | What it does | Default |
| --- | --- | --- |
| `TUNERS` | Streams that may play at the same time. | `4` |
| `MIN_HEIGHT` | Lowest resolution `tellyard check` accepts, such as `1080`. `off` accepts any. A channel's own `min_height` in `channels.toml` overrides it. | `off` |
| `DEVICE_NAME` | Name shown in Plex. | `Tellyard` |
| `DEVICE_ID` | 8 hexadecimal characters. Plex remembers the tuner by this. | derived from the name |
| `BASE_URL` | Address Plex should use, such as `http://192.168.1.10:5004`. Set it when the address Plex connects with is not reachable for streams. | from the request |
| `GUIDE_DAYS` | Days of listings to publish. | `3` |
| `GUIDE_REFRESH_HOURS` | How often the guide is rebuilt. | `6` |
| `YTDLP_ARGS` | Extra yt-dlp arguments, for example extractor options. | |
| `LOG_LEVEL` | `debug`, `info`, `warning` or `error`. | `info` |
| `LISTEN_PORT` | Port inside the container. If you change it, change the right side of `ports:` too. | `5004` |
| `CHANNELS_FILE` | The channel list inside the container. Keep the default. | `/config/channels.toml` |
| `DATA_DIR` | Folder inside the container for the guide and caches. Keep the default. | `/data` |

## HTTP endpoints

| Path | Use |
| --- | --- |
| `/discover.json`, `/lineup.json`, `/lineup_status.json`, `/device.xml` | The HDHomeRun API Plex reads. |
| `/stream/<number>` | One channel as MPEG-TS. Also at `/auto/v<number>`. |
| `/guide.xml` | The XMLTV guide. |
| `/playlist.m3u` | An M3U playlist for players such as VLC. |
| `/health` | Status as JSON. |

## Development

```sh
uv sync
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
```

Running the service locally needs `ffmpeg` and, for YouTube channels, `deno` on the `PATH`.
