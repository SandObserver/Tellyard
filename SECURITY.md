# Security

## Reporting a vulnerability

Use **Report a vulnerability** on the repository's Security tab. Do not open a public issue.

## Supported versions

Only the latest release gets fixes.

## Running it safely

- Tellyard has no login. Anyone who can reach its port can list the channels, read the guide and start streams. Keep port 5004 on your home network. Do not forward it to the internet.
- To listen on one network only, put that address before the port, such as `"192.168.1.10:5004:5004"`. Docker otherwise publishes the port on every network the host is on.
- Tellyard reaches private addresses only for hosts written in `channels.toml` or the settings, even when a stream or redirect points elsewhere. Behind a proxy (`http_proxy`, `https_proxy`) only numeric private addresses are refused. See [Addresses Tellyard reaches](docs/how-it-works.md#addresses-tellyard-reaches).
- `channels.toml` can hold `Referer` headers and token service addresses. Treat it like a private file and keep it out of public repositories.
- The image runs as user 1000 with a read-only file system and no capabilities. Keep the `read_only`, `cap_drop` and `no-new-privileges` lines of the example compose file.
