<p align="center"><img src="docs/logo.svg" width="96" alt=""></p>

<h1 align="center">Tellyard</h1>

<p align="center"><b>A live TV tuner you'll forget is running.</b></p>

<p align="center">
  <a href="https://github.com/SandObserver/Tellyard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/SandObserver/Tellyard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/SandObserver/Tellyard/pkgs/container/tellyard"><img alt="ghcr.io" src="https://img.shields.io/badge/ghcr.io-tellyard-2496ED?logo=github&logoColor=white"></a>
  <a href="https://hub.docker.com/r/sandobserver/tellyard"><img alt="Docker Hub" src="https://img.shields.io/badge/docker%20hub-tellyard-2496ED?logo=docker&logoColor=white"></a>
</p>

<p align="center"><b><a href="https://tellyard.sandobserver.com">tellyard.sandobserver.com</a></b></p>

Tellyard presents itself to Plex and Jellyfin as an HDHomeRun network tuner. You list your channels in one file. When a viewer tunes in, Tellyard finds the channel's current stream, picks its best quality and hands it over without re-encoding. When the viewer leaves, it stops. Nothing is downloaded while nobody watches, and it runs as one small container with no database.

## What you get

- **On demand.** A stream runs only while someone watches, and up to `TUNERS` at once.
- **YouTube, Twitch, web players and HLS.** Live channels on YouTube, Twitch, Kick and other sites through yt-dlp, broadcaster pages that embed an HLS playlist, and direct HLS playlists. The stream is found again on every tune, so expiring links never break playback.
- **A guide that is never empty.** Real schedules from any XMLTV source, with placeholder blocks in every gap. Shows are filed as TV episodes, with a category.
- **Tries to reconnect.** A stalled or dropped source is restarted on its own, up to three tries in a row.
- **Quality check.** `tellyard check` tunes each channel and reports the resolution it really sends, so a channel below your minimum never slips in.
- **One file.** Channels live in `channels.toml`, reloaded while Tellyard runs.

## Getting started

You need [Docker](https://docs.docker.com/get-started/get-docker/) on a 64-bit Intel, AMD or ARM machine, and Plex
or Jellyfin. Watching live TV is free; recording in Plex needs Plex Pass.

### 1. Write your channel list

Create a `config` folder and copy [`channels.example.toml`](channels.example.toml) into it as
`channels.toml`. Replace the examples with your channels. See [Channels](docs/channels.md).

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

Tellyard has no login. Anyone who can reach port 5004 can start streams, so keep it on your home
network and do not forward the port to the internet. See [Security](SECURITY.md).

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

### 5. Add it to your media server

Tellyard does not announce itself on the network, so enter its address by hand: your server's
address with port 5004, for example `192.168.1.10:5004`. Its guide is
`http://192.168.1.10:5004/guide.xml`.

- **Plex:** **Settings > Live TV & DVR > Set up Plex DVR**. Enter the address, then choose
  **Have an XMLTV guide on your server? Click here to use it** and enter the guide address. On an
  iPhone, iPad or Apple TV, turn on **Force Direct Play** in the Plex app. See [Plex](docs/plex.md).
- **Jellyfin:** **Dashboard > Live TV**. Add a tuner device of type HDHomeRun with the address,
  then add an XMLTV guide provider with the guide address.

## Unraid

Open **Apps**, search for **Tellyard** and choose **Install**. It starts with no channels. Put your
list in place from the Unraid terminal, starting from the example:

```sh
curl -fsSL -o /mnt/user/appdata/tellyard/config/channels.toml \
  https://raw.githubusercontent.com/SandObserver/Tellyard/main/channels.example.toml
nano /mnt/user/appdata/tellyard/config/channels.toml
```

Tellyard loads the file within a minute. Check the channels with `docker exec Tellyard tellyard check`
(on Unraid the container is named `Tellyard`), then add it to your media server as in step 5, with
your Unraid server's address. The template runs as Unraid's `nobody` user (`--user=99:100`) with the
same lockdown as the compose file, and restarts the container after a crash.

## Channels

Each channel in `channels.toml` needs a number, a name, a logo and one source:

```toml
[[channel]]
number = 101
name = "Example News"
logo = "https://example.com/logos/example-news.png"
hls = "https://example.com/live/news/master.m3u8"
[channel.guide]
category = "News"
```

[Channels](docs/channels.md) lists every option and how to find sources, logos and guides.
Tellyard reloads the file within a minute of a change and keeps the previous channels if the new
file has a mistake. After adding or removing channels, refresh the guide in Plex.

Only add streams you are allowed to watch.

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
| `health` | Exit 0 when the running service is healthy, or print why it is not. Used by the container health check. |

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
| `HEARTBEAT_URL` | Address Tellyard calls once a minute while it is healthy, for a monitor such as Uptime Kuma or Healthchecks. | |
| `LISTEN_PORT` | Port inside the container. If you change it, change the right side of `ports:` too. | `5004` |
| `CHANNELS_FILE` | The channel list inside the container. Keep the default. | `/config/channels.toml` |
| `DATA_DIR` | Folder inside the container for the guide and caches. Keep the default. | `/data` |

## Documentation

- [Channels](docs/channels.md): every option, finding sources, guides and logos.
- [Plex](docs/plex.md): adding Tellyard, refreshing the guide, and settings for the Plex apps.
- [How it works](docs/how-it-works.md): tuning, reconnects, the guide and the HTTP endpoints.
- [Troubleshooting](docs/troubleshooting.md): log messages and what to do.
- [Upgrade, back up, uninstall](docs/maintenance.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and releases. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Legal

Tellyard is an independent project. It is not affiliated with or endorsed by Plex, SiliconDust
(HDHomeRun), YouTube, Twitch, Kick or any broadcaster. Their names appear only to say what Tellyard works with.

Tellyard ships no channels, stream addresses or logos. It plays only the sources you list. It
contains no code to decrypt DRM or to get around logins, paywalls or region limits. You are
responsible for making sure you may watch each source you add and that your use follows its
terms.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for the name and the third-party software in
the container image.
