#!/bin/bash
# webm -> optimized GIF via the worker container's ffmpeg (host has none).
# usage: convert.sh <sceneName> [width] [fps] [dither] [start-trim-s] [max-colors]
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
NAME="$1"; W="${2:-960}"; FPS="${3:-12}"; DITHER="${4:-sierra2_4a}"; TRIM="${5:-0}"; COLORS="${6:-200}"
C=poindexter-prefect-worker

docker cp -q "$S/video/$NAME.webm" "$C:/tmp/$NAME.webm"
docker exec "$C" ffmpeg -y -v error -ss "$TRIM" -i "/tmp/$NAME.webm" \
  -vf "fps=$FPS,scale=$W:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=$COLORS:stats_mode=diff[p];[b][p]paletteuse=dither=$DITHER:diff_mode=rectangle" \
  "/tmp/$NAME.gif"
docker exec "$C" sh -c "ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/$NAME.gif"
OUT="$(dirname "$S")/$NAME.gif"
docker cp -q "$C:/tmp/$NAME.gif" "$OUT"
docker exec -u 0 "$C" rm -f "/tmp/$NAME.webm" "/tmp/$NAME.gif"
ls -lh "$OUT"
