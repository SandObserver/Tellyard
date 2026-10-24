FROM ghcr.io/astral-sh/uv:0.13.2@sha256:b9fddcde095a7aeafd499024adb8927df6619c7c5672f5ff7126474715f62ba4 AS uv
# tests/e2e.sh encodes its test stream with this full build. The image ships the smaller one below.
FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg-full
FROM denoland/deno:bin-2.9.7@sha256:bc5aa4466e21b6d3021226a85ba2e1911f7c386254d97b9d797903ab74edace2 AS deno

FROM --platform=$BUILDPLATFORM tonistiigi/xx:1.9.0@sha256:c64defb9ed5a91eacb37f96ccc3d4cd72521c4bd18d5442905b95e2226b0e707 AS xx
FROM --platform=$BUILDPLATFORM alpine:3.24.2@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS ffmpeg
COPY --from=xx / /
ARG TARGETPLATFORM
ARG FFMPEG_VERSION=9.0.2
ARG FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
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
    && llvm-strip ffmpeg && xx-verify --static ffmpeg && mv ffmpeg /ffmpeg

FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
# yt-dlp needs deno for YouTube. Caches stay in /data.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    CHANNELS_FILE=/config/channels.toml DATA_DIR=/data \
    XDG_CACHE_HOME=/data/cache DENO_DIR=/data/cache/deno
COPY --from=ffmpeg /ffmpeg /usr/local/bin/
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=build /app/.venv /app/.venv
# The base image has no standard library bytecode, and user 1000 cannot write it.
RUN python -m compileall -q -j0 -x '/(site-packages|idlelib|tkinter|turtledemo|ensurepip|pydoc_data)/' \
      "$(python -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')" \
    && mkdir /config /data && chown 1000:1000 /data
COPY channels.example.toml /config/channels.example.toml
USER 1000:1000
VOLUME /data
EXPOSE 5004
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD ["python", "-m", "tellyard.health"]
CMD ["tellyard", "serve"]
