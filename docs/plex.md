# Plex

[Back to the README](../README.md)

## Adding Tellyard

1. In Plex, open **Settings > Live TV & DVR > Set up Plex DVR**.
2. Plex does not find Tellyard on its own. Enter its address by hand, such as `192.168.1.10:5004`.
3. For the guide, choose **Have an XMLTV guide on your server? Click here to use it**, and enter `http://192.168.1.10:5004/guide.xml`.
4. Plex lists the channels. Keep them all, and finish.

Plex remembers the tuner by `DEVICE_ID`. Keep it the same, or Plex sees a new tuner.

If Plex reaches Tellyard through an address that does not work for streams, such as a container name, set `BASE_URL` to the address Plex should use.

## After changing channels

Tellyard reloads `channels.toml` on its own, but Plex keeps its own copy of the channel list. After adding or removing a channel, open the DVR under **Settings > Live TV & DVR** and choose **Refresh Guide**.

## Plex apps

- **iPhone, iPad and Apple TV:** turn on **Force Direct Play** under **Settings > Video & Audio**. Without it, these apps ask Plex to convert Live TV and every other tune ends in "Can't Reach Server". With it, every channel plays, though every other tune can take about 20 seconds longer to start.
- Show detail pages from an XMLTV guide load empty in the same apps. Plex Web shows them.
- Plex Web needs no changes.

Watching live TV is free. Recording needs Plex Pass.
