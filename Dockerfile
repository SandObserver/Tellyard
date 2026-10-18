FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv
FROM mwader/static-ffmpeg:9.0.2-1@sha256:f5d100184884eb3e82b0eb14bb5ebf98da99826ac824989720297fb9ef21c822 AS ffmpeg
FROM denoland/deno:bin-2.9.7@sha256:bc5aa4466e21b6d3021226a85ba2e1911f7c386254d97b9d797903ab74edace2 AS deno

FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
# yt-dlp needs deno for YouTube. Caches stay in /data.
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 \
    CHANNELS_FILE=/config/channels.toml DATA_DIR=/data \
    XDG_CACHE_HOME=/data/cache DENO_DIR=/data/cache/deno
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/
COPY --from=deno /deno /usr/local/bin/deno
COPY --from=build /app/.venv /app/.venv
RUN mkdir /config /data && chown 1000:1000 /data
COPY channels.example.toml /config/channels.example.toml
USER 1000:1000
VOLUME /data
EXPOSE 5004
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD ["tellyard", "health"]
CMD ["tellyard", "serve"]
