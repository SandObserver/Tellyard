FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv
# tests/e2e.sh encodes its test stream with this full build. The image ships the smaller one below.
FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg-full
FROM denoland/deno:bin-2.9.8@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 AS deno

FROM --platform=$BUILDPLATFORM tonistiigi/xx:1.10.0@sha256:40fe21f44bb8cddf1eb1226db190d0a984118f62ee02a91b77d99448d28264a6 AS xx
FROM --platform=$BUILDPLATFORM alpine:3.24.3@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS ffmpeg
COPY --from=xx / /
ARG TARGETPLATFORM
ARG FFMPEG_VERSION=9.1
ARG FFMPEG_SHA256=1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433
WORKDIR /src
# ffmpeg only copies HLS into MPEG-TS and reads the stream size for tellyard check. The relay does all HTTPS.
# musl gives threads a small stack. ffmpeg crashes with it.
RUN apk add --no-cache build-base clang lld llvm nasm \
    && xx-apk add --no-cache gcc musl-dev \
    && wget -q "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" \
    && echo "$FFMPEG_SHA256  ffmpeg-$FFMPEG_VERSION.tar.xz" | sha256sum -c - \
    && tar -xf "ffmpeg-$FFMPEG_VERSION.tar.xz" --strip-components 1 \
    && ./configure --enable-cross-compile --target-os=linux --arch="$(xx-info march)" \
      --cc=xx-clang --ar=llvm-ar --nm=llvm-nm --ranlib=llvm-ranlib --strip=llvm-strip \
      --disable-everything --disable-autodetect --disable-doc --disable-debug \
      --disable-ffprobe --disable-ffplay --disable-shared --enable-static \
      --extra-ldflags='-static -fuse-ld=lld -Wl,-z,stack-size=2097152' \
      --enable-protocol=file,pipe,http,tcp,crypto,data \
      --enable-demuxer=hls,mpegts,mov,aac,ac3,eac3,mp3,webvtt,h264,hevc \
      --enable-muxer=mpegts --enable-parsers --enable-bsfs \
      --enable-decoder=h264,hevc,aac,aac_latm,ac3,eac3,mp3,mp2,mpeg2video \
    && make -j"$(nproc)" ffmpeg \
    && llvm-strip ffmpeg && xx-verify --static ffmpeg && mv ffmpeg /ffmpeg \
    && mkdir /licenses && cp LICENSE.md COPYING.LGPLv2.1 /licenses/ \
    && printf 'https://ffmpeg.org/releases/ffmpeg-%s.tar.xz\nSHA-256: %s\n' "$FFMPEG_VERSION" "$FFMPEG_SHA256" \
      > /licenses/SOURCE

FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    CHANNELS_FILE=/config/channels.toml DATA_DIR=/data
COPY --from=ffmpeg /ffmpeg /usr/local/bin/
# yt-dlp needs deno for YouTube.
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=build /app/.venv /app/.venv
RUN apt-get update && apt-get install -y --no-install-recommends tini=0.19.0-3+b8 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m compileall -q -j0 -x '/(site-packages|idlelib|tkinter|turtledemo|ensurepip|pydoc_data)/' \
      "$(python -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')" \
    && mkdir /config /data && chown 1000:1000 /data
COPY channels.example.toml /config/channels.example.toml
COPY LICENSE NOTICE licenses /usr/share/doc/tellyard/
COPY --from=ffmpeg /licenses /usr/share/doc/tellyard/ffmpeg/
USER 1000:1000
VOLUME /data
EXPOSE 5004
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD ["python", "-m", "tellyard.health"]
# tini reaps the helpers yt-dlp leaves behind. -s also works under docker --init.
ENTRYPOINT ["tini", "-s", "--"]
CMD ["tellyard", "serve"]
