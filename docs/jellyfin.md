# Jellyfin

[Back to the README](../README.md)

## Adding Tellyard

1. In the admin dashboard, open **Live TV**.
2. Choose **+** next to **Tuner Devices**. Set **Tuner Type** to **HD Homerun** and **Tuner IP Address** to Tellyard's address with its port, such as `http://192.168.1.10:5004`. Save.
3. Choose **+** next to **TV Guide Data Providers**, then **XMLTV**. Enter `http://192.168.1.10:5004/guide.xml` and save.

**Detect My Devices** does not find Tellyard. Enter the address by hand. Jellyfin reaches Tellyard over HTTP only, so it does not need host networking.

## After changing channels

Jellyfin keeps its downloaded copy of the guide for up to an hour. After adding or removing a channel, run the **Refresh Guide** task under **Scheduled Tasks** in the admin dashboard. If the guide still shows the old channels, run it again an hour later.
