# Upgrade, back up, uninstall

[Back to the README](../README.md)

## Upgrading

```sh
docker compose pull && docker compose up -d
```

Each release carries the newest yt-dlp at the time, which keeps YouTube channels working. Stay on the latest release.

## Backups

Back up `config/channels.toml`. Everything in `./data` is rebuilt when missing.

## Uninstalling

1. In Plex, open **Settings > Live TV & DVR**, open the Tellyard DVR and delete it.
2. Stop and remove the container: `docker compose down`.
3. Delete the `config` and `data` folders.
