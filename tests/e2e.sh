#!/usr/bin/env bash
# Plays a generated live stream through the image. Needs Docker, not the internet.
# Usage: tests/e2e.sh [IMAGE]
set -euo pipefail

image=${1:-tellyard:ci}
name=tellyard-e2e-$$
work=$(mktemp -d)
chmod 755 "$work"
mkdir -p "$work/media" "$work/config"

cleanup() {
  docker rm -f "$name" "$name-source" "$name-web" >/dev/null 2>&1 || true
  docker network rm "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*"
  docker logs "$name" 2>&1 | tail -40 || true
  exit 1
}

docker network create "$name" >/dev/null

docker run -d --name "$name-source" --network "$name" --user "$(id -u):$(id -g)" \
  -v "$work/media:/media" --entrypoint ffmpeg "$image" -hide_banner -loglevel error -re \
  -f lavfi -i testsrc2=size=1920x1080:rate=25 -f lavfi -i sine=frequency=440:sample_rate=48000 \
  -c:v libx264 -preset ultrafast -g 50 -c:a aac \
  -f hls -hls_time 2 -hls_list_size 6 -hls_flags delete_segments /media/video.m3u8 >/dev/null
cat > "$work/media/master.m3u8" <<'M3U'
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080
video.m3u8
M3U
docker run -d --name "$name-web" --network "$name" -v "$work/media:/media:ro" \
  --entrypoint python "$image" -m http.server -d /media 8080 >/dev/null

cat > "$work/config/channels.toml" <<TOML
[[channel]]
number = 101
name = "Test Pattern"
logo = "https://example.com/logo.png"
hls = "http://$name-web:8080/master.m3u8"
[channel.guide]
category = "News"
TOML
chmod 755 "$work/config"
chmod 644 "$work/config/channels.toml"

for _ in $(seq 30); do
  [ -s "$work/media/video.m3u8" ] && break
  sleep 1
done
[ -s "$work/media/video.m3u8" ] || fail "the test stream did not start"

docker run -d --name "$name" --network "$name" --read-only --tmpfs /tmp --tmpfs /data:uid=1000,gid=1000 \
  --cap-drop ALL --security-opt no-new-privileges:true -e MIN_HEIGHT=1080 \
  -v "$work/config:/config:ro" -p 127.0.0.1::5004 "$image" >/dev/null
port=$(docker port "$name" 5004/tcp | head -1 | sed 's/.*://')
base=http://127.0.0.1:$port

for _ in $(seq 30); do
  curl -sf "$base/healthz" >/dev/null && break
  sleep 1
done
curl -sf "$base/healthz" >/dev/null || fail "the service did not answer"

echo "== check"
docker exec "$name" tellyard check | tee "$work/check.txt" || fail "tellyard check failed"
grep -q "101  Test Pattern  *1080p  ok" "$work/check.txt" || fail "check did not pick 1080p"

echo "== lineup"
curl -sf "$base/lineup.json" | grep -q '"GuideName": "Test Pattern"' || fail "the line-up misses the channel"

echo "== guide"
for _ in $(seq 30); do
  curl -sf "$base/guide.xml" -o "$work/guide.xml" && break
  sleep 1
done
grep -q '<channel id=' "$work/guide.xml" || fail "the guide has no channel"
grep -q '<programme ' "$work/guide.xml" || fail "the guide has no programmes"

echo "== stream"
curl -s --max-time 8 "$base/stream/101" -o "$work/out.ts" || [ $? -eq 28 ] || fail "the stream request failed"
{ docker run --rm -v "$work:/work:ro" --entrypoint ffmpeg "$image" -hide_banner -i /work/out.ts 2>&1 || true; } \
  | grep 'Stream #' | tee "$work/probe.txt"
grep -q 'Video: .*, 1920x1080' "$work/probe.txt" || fail "the stream has no 1080p video"
grep -q 'Audio: ' "$work/probe.txt" || fail "the stream has no audio"

echo "== stop"
running_ffmpeg() {
  local processes
  processes=$(docker top "$name") || fail "docker top failed"
  grep -q '[f]fmpeg' <<<"$processes"
}
for _ in $(seq 15); do
  running_ffmpeg || break
  sleep 1
done
running_ffmpeg && fail "ffmpeg still runs after the viewer left"
docker logs "$name" 2>&1 | grep -q 'msg="stream stopped" channel=101' || fail "the stream did not stop"

echo "e2e passed"
