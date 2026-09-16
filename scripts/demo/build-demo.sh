#!/usr/bin/env bash
# Build the README demo GIF end-to-end against the live local stack.
#   ./build-demo.sh                  all phases
#   ./build-demo.sh --from PHASE     create | timelapse | approve | site | stitch
# See README.md in this folder for prerequisites and knobs.
set -euo pipefail
cd "$(dirname "$0")"

DEMO_TOPIC="${DEMO_TOPIC:-Self-hosting Qwen 3 on a 5090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
FRAME_INTERVAL="${FRAME_INTERVAL:-5}"
TIMELAPSE_FPS="${TIMELAPSE_FPS:-6}"
MAX_WAIT_MIN="${MAX_WAIT_MIN:-30}"
GIF_WIDTH="${GIF_WIDTH:-960}"
GIF_FPS="${GIF_FPS:-10}"
SITE_URL="${SITE_URL:-https://www.gladlabs.io}"
FONT="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf}"
OUT_DIR="../../docs/assets/readme"

FROM="create"
[[ "${1:-}" == "--from" ]] && FROM="${2:?--from needs a phase}"

phase_ge() { # true if $FROM is at-or-before $1 in the run order
  local order="create timelapse approve site stitch" a b i=0 j=0 k=0
  for p in $order; do k=$((k+1)); [[ "$p" == "$FROM" ]] && i=$k; [[ "$p" == "$1" ]] && j=$k; done
  [[ $i -le $j ]]
}

need() { command -v "$1" >/dev/null || { echo "FATAL: '$1' not found — see README.md prerequisites"; exit 1; }; }
need vhs; need ffmpeg; need node; need poindexter
mkdir -p work "$OUT_DIR"

caption() { # caption <in.mp4> <out.mp4> <text>  — bottom banner, README-consistent styling
  ffmpeg -y -loglevel error -i "$1" -vf \
    "scale=1200:-2,drawtext=fontfile=${FONT}:text='$3':fontsize=30:fontcolor=#c9d5e1:box=1:boxcolor=#0d1117@0.85:boxborderw=14:x=(w-text_w)/2:y=h-th-26" \
    -an "$2"
}

# ---------------------------------------------------------------- create
if phase_ge create; then
  echo "== Phase A: queue a topic (vhs) =="
  sed "s/__DEMO_TOPIC__/${DEMO_TOPIC//\//\\/}/" demo-create.tape.tpl > work/demo-create.tape
  vhs work/demo-create.tape
  # Pull the task id out of what the CLI actually printed during the recording
  # by re-asking the API: newest task should be ours.
  TASK_ID="$(poindexter tasks list 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|^[[:space:]]*[0-9]{2,6}[[:space:]]' | head -1 | tr -d '[:space:]')" || true
  if [[ -z "${TASK_ID}" ]]; then
    echo "Could not auto-detect the new task id from 'poindexter tasks list'."
    read -rp "Paste the task id from the recording: " TASK_ID
  fi
  echo "$TASK_ID" > work/task_id
  echo "task id: $TASK_ID"
fi

TASK_ID="$(cat work/task_id 2>/dev/null || true)"
[[ -z "$TASK_ID" && "$FROM" != "stitch" ]] && { echo "FATAL: work/task_id missing — run from create, or echo the id into work/task_id"; exit 1; }

# ------------------------------------------------------------- timelapse
if phase_ge timelapse; then
  echo "== Phase B: Grafana timelapse while the pipeline runs =="
  rm -rf work/frames
  GRAFANA_URL="$GRAFANA_URL" FRAME_INTERVAL="$FRAME_INTERVAL" node capture-frames.mjs timelapse &
  CAP_PID=$!
  trap '[[ -n "${CAP_PID:-}" ]] && kill "$CAP_PID" 2>/dev/null || true' EXIT
  echo "waiting for task $TASK_ID to reach awaiting_approval (max ${MAX_WAIT_MIN} min)..."
  DEADLINE=$(( $(date +%s) + MAX_WAIT_MIN * 60 ))
  while true; do
    if poindexter tasks list --status awaiting_approval 2>/dev/null | grep -q "$TASK_ID"; then
      echo "task is awaiting approval — stopping capture"; break
    fi
    if (( $(date +%s) > DEADLINE )); then
      echo "WARN: hit MAX_WAIT_MIN — stopping capture anyway (check the run in Prefect)"; break
    fi
    sleep 15
  done
  kill "$CAP_PID" 2>/dev/null || true; wait "$CAP_PID" 2>/dev/null || true; trap - EXIT
  N=$(ls work/frames/frame-*.png 2>/dev/null | wc -l)
  (( N < 10 )) && { echo "FATAL: only $N timelapse frames captured"; exit 1; }
  echo "assembling $N frames at ${TIMELAPSE_FPS} fps"
  ffmpeg -y -loglevel error -framerate "$TIMELAPSE_FPS" -pattern_type glob -i 'work/frames/frame-*.png' \
    -pix_fmt yuv420p work/segment-b-raw.mp4
fi

# --------------------------------------------------------------- approve
if phase_ge approve; then
  echo "== Phase C: the approval moment (vhs) =="
  sed "s/__TASK_ID__/${TASK_ID}/" demo-approve.tape.tpl > work/demo-approve.tape
  vhs work/demo-approve.tape
fi

# ------------------------------------------------------------------ site
if phase_ge site; then
  echo "== Phase D: live-site reveal =="
  echo "   (waiting 60 s for publish + deploy to settle — bump if your deploy is slower)"
  sleep "${PUBLISH_WAIT:-60}"
  rm -rf work/site-frames
  node capture-frames.mjs site "$SITE_URL"
  ffmpeg -y -loglevel error -framerate 10 -pattern_type glob -i 'work/site-frames/frame-*.png' \
    -pix_fmt yuv420p work/segment-d-raw.mp4
fi

# ---------------------------------------------------------------- stitch
echo "== Stitch: captions → concat → mp4 + gif =="
caption work/segment-a-create.mp4 work/cap-a.mp4 "1 · queue a topic"
caption work/segment-b-raw.mp4    work/cap-b.mp4 "2 · the pipeline runs — QA rails scoring (sped up)"
caption work/segment-c-approve.mp4 work/cap-c.mp4 "3 · survivors wait for one-click approval"
caption work/segment-d-raw.mp4    work/cap-d.mp4 "4 · live on gladlabs.io"

printf "file 'cap-a.mp4'\nfile 'cap-b.mp4'\nfile 'cap-c.mp4'\nfile 'cap-d.mp4'\n" > work/concat.txt
ffmpeg -y -loglevel error -f concat -safe 0 -i work/concat.txt -c:v libx264 -crf 22 -pix_fmt yuv420p -an "$OUT_DIR/demo.mp4"

make_gif() { # make_gif <width> <fps>
  ffmpeg -y -loglevel error -i "$OUT_DIR/demo.mp4" -vf \
    "fps=$2,scale=$1:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
    "$OUT_DIR/demo.gif"
}
make_gif "$GIF_WIDTH" "$GIF_FPS"
SIZE=$(stat -c%s "$OUT_DIR/demo.gif")
if (( SIZE > 9500000 )); then
  echo "gif is $((SIZE/1024/1024)) MB — retrying smaller (840px / 8 fps)"
  make_gif 840 8
  SIZE=$(stat -c%s "$OUT_DIR/demo.gif")
fi

echo ""
echo "done:"
echo "  $OUT_DIR/demo.gif  ($((SIZE/1024)) KB)"
echo "  $OUT_DIR/demo.mp4"
echo ""
echo "REVIEW BEFORE COMMITTING: watch the gif end-to-end and frame-scrub the"
echo "Grafana section for hostnames / tailnet IPs / private panel strings."
