import time

import pytest

from tellyard import hls

MUXED = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
720/index.m3u8
#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=90000,RESOLUTION=1920x1080,URI="iframe.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=4500000,RESOLUTION=1920x1080
https://cdn.example.com/1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=854x480
480/index.m3u8
"""

SEPARATE_AUDIO = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="Described",DEFAULT=NO,URI="audio/described.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",DEFAULT=YES,URI="audio/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",URI="subs/en.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO="aac",SUBTITLES="subs"
video/1080.m3u8
"""


def test_picks_the_tallest_variant_and_resolves_urls() -> None:
    variants, renditions = hls.parse_master(MUXED, "https://origin.example.com/live/master.m3u8")
    assert len(variants) == 3
    choice = hls.choose(variants, renditions)
    assert choice == hls.Choice("https://cdn.example.com/1080/index.m3u8", None, 1080)


def test_relative_variants_join_the_base() -> None:
    variants, _ = hls.parse_master(MUXED, "https://origin.example.com/live/master.m3u8")
    assert variants[0].uri == "https://origin.example.com/live/720/index.m3u8"


def test_separate_audio_prefers_the_default_rendition() -> None:
    variants, renditions = hls.parse_master(SEPARATE_AUDIO, "https://a.example.com/x/master.m3u8")
    choice = hls.choose(variants, renditions)
    assert choice.audio == "https://a.example.com/x/audio/en.m3u8"
    assert choice.video == "https://a.example.com/x/video/1080.m3u8"


def test_without_resolution_the_highest_bandwidth_wins() -> None:
    text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=900\nhigh.m3u8\n"
    variants, renditions = hls.parse_master(text, "https://a.example.com/")
    choice = hls.choose(variants, renditions)
    assert choice.video.endswith("high.m3u8")
    assert choice.height is None


def test_audio_group_without_renditions_is_ignored() -> None:
    text = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1,RESOLUTION=1920x1080,AUDIO="missing"\nv.m3u8\n'
    variants, renditions = hls.parse_master(text, "https://a.example.com/")
    assert hls.choose(variants, renditions).audio is None


def test_detects_master_playlists() -> None:
    assert hls.is_master(MUXED)
    assert not hls.is_master("#EXTM3U\n#EXTINF:6,\nseg1.ts\n")


@pytest.mark.parametrize("text", ["<html>", "", "#EXTM3U\n#EXT-X-VERSION:3\n"])
def test_rejects_non_master_text(text: str) -> None:
    with pytest.raises(hls.PlaylistError):
        hls.parse_master(text, "https://a.example.com/")


def test_a_long_tag_line_parses_in_linear_time() -> None:
    text = "#EXTM3U\n#EXT-X-STREAM-INF:" + "A" * 4_000_000 + "\nv.m3u8\n"
    started = time.monotonic()
    variants, _ = hls.parse_master(text, "https://a.example.com/")
    assert time.monotonic() - started < 2
    assert variants[0].bandwidth == 0


def test_quoted_attributes_keep_their_commas() -> None:
    assert hls.attributes('BANDWIDTH=1,CODECS="avc1.4d401f,mp4a.40.2",RESOLUTION=1x2') == {
        "BANDWIDTH": "1",
        "CODECS": "avc1.4d401f,mp4a.40.2",
        "RESOLUTION": "1x2",
    }


def test_default_audio_in_the_video_stream_is_kept() -> None:
    text = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES\n'
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Spanish",DEFAULT=NO,URI="es/audio.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,AUDIO="aud"\n'
        "v/1080.m3u8\n"
    )
    variants, renditions = hls.parse_master(text, "https://a.example.com/live/master.m3u8")
    assert hls.choose(variants, renditions).audio is None


def test_only_http_addresses_are_used() -> None:
    text = (
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",DEFAULT=YES,URI="file:///config/channels.toml"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=9,RESOLUTION=3840x2160,AUDIO="aud"\n'
        "gopher://192.0.2.5:6379/1x\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=5,RESOLUTION=1920x1080,AUDIO="aud"\n'
        "v/1080.m3u8\n"
    )
    variants, renditions = hls.parse_master(text, "https://a.example.com/live/master.m3u8")
    choice = hls.choose(variants, renditions)
    assert choice == hls.Choice("https://a.example.com/live/v/1080.m3u8", None, 1080)
    with pytest.raises(hls.PlaylistError, match="no variants"):
        hls.parse_master("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nfile:///etc/passwd\n", "https://a.example.com/")


def test_a_bandwidth_that_is_not_a_number_counts_as_zero() -> None:
    variants, _ = hls.parse_master("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=²\nv.m3u8\n", "https://a.example.com/")
    assert variants[0].bandwidth == 0
