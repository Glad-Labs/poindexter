#!/usr/bin/env bash
# bake-clips.sh — re-record the VHS demo-clip library (Glad-Labs/poindexter#937).
#
# Runs the bake as a THROWAWAY container rather than inside the long-lived
# worker, for two reasons:
#
#   1. VHS drives headless Chromium, whose sandbox needs
#      `--security-opt seccomp=unconfined`. Granting that to the main worker
#      would permanently widen the attack surface of the container that
#      handles external content and LLM output, for a job that runs weekly.
#      Scoped to a container that lives ~60s and only runs read-only CLI
#      commands, it is a much smaller grant.
#
#   2. Triggering from a scheduled job INSIDE a container would require the
#      Docker socket, which is root-equivalent on the host — a far larger
#      privilege than the seccomp relaxation it would avoid. A host-side
#      systemd timer needs no new privilege anywhere.
#
# Measured cost of a full bake (15 tapes): peak ~2 GB RSS, CPU bursting to
# ~13 cores, ~4-5 minutes wall. Idle cost is zero — nothing runs between
# bakes. The CPU burst is why this defers when the box is already busy (see
# the load check below): overlapping a video render would contend with
# ffmpeg, and overlapping a content run would contend with Ollama.
#
# Operators: set POINDEXTER_IMAGE / POINDEXTER_CLIP_DIR if your install
# differs from the defaults. Exit codes: 0 baked (or deliberately deferred),
# 1 bake failure, 2 preconditions unmet.

set -euo pipefail

IMAGE="${POINDEXTER_IMAGE:-glad-labs-website-worker}"
WORKER_CONTAINER="${POINDEXTER_WORKER_CONTAINER:-poindexter-worker}"
CLIP_DIR="${POINDEXTER_CLIP_DIR:-${HOME}/.poindexter/demo-clips}"
# Bake the code that is DEPLOYED, not whatever was baked into the image at its
# last build. The tapes live in the repo, so without this a newly-added tape
# would silently not be recorded until someone rebuilt the worker image — the
# same class of staleness the compose `POINDEXTER_DEPLOY_ROOT` anchoring
# guards against (see reference: a bare './' mount resolves to the compose
# project dir, not the deploy clone). Defaults to the checkout this script
# ships in; override to point at a separate deploy clone.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_ROOT="${POINDEXTER_DEPLOY_ROOT:-$(cd -- "${SCRIPT_DIR}/../.." && pwd)}"
CONTAINER_CLIP_DIR="/home/appuser/.poindexter/demo-clips"
# Defer if the 1-minute load average exceeds this fraction of the core count.
# The bake itself peaks near 13 cores, so starting one on an already-loaded
# box is what turns "invisible background job" into "why did my render stall".
LOAD_FRACTION="${POINDEXTER_BAKE_MAX_LOAD_FRACTION:-0.6}"

log() { printf '[bake-clips] %s\n' "$*"; }

# --- preconditions ---------------------------------------------------------

command -v docker >/dev/null 2>&1 || { log "docker not on PATH"; exit 2; }

if [ ! -d "${DEPLOY_ROOT}/src/cofounder_agent/demo_tapes" ]; then
  log "no demo_tapes/ under ${DEPLOY_ROOT} — set POINDEXTER_DEPLOY_ROOT"; exit 2
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "image '$IMAGE' not found — build the stack first"; exit 2
fi

if ! docker ps --format '{{.Names}}' | grep -qx "$WORKER_CONTAINER"; then
  # The bake shares the worker's network namespace to reach the API on
  # localhost:8002, so the worker must be up.
  log "worker container '$WORKER_CONTAINER' is not running"; exit 2
fi

# The container runs as appuser (uid 1001) while the host dir belongs to the
# operator (uid 1000), so a default-permission directory makes the bake die on
# PermissionError writing its first file. Mirrors the 777 the other appuser
# media dirs already use (generated-videos / podcast / generated-images).
if [ ! -d "$CLIP_DIR" ]; then
  log "creating $CLIP_DIR"
  mkdir -p "$CLIP_DIR"
fi
chmod 777 "$CLIP_DIR"

# --- quiet-window check ----------------------------------------------------

CORES="$(nproc)"
LOAD1="$(awk '{print $1}' /proc/loadavg)"
THRESHOLD="$(awk -v c="$CORES" -v f="$LOAD_FRACTION" 'BEGIN{printf "%.2f", c*f}')"
if awk -v l="$LOAD1" -v t="$THRESHOLD" 'BEGIN{exit !(l > t)}'; then
  # Deliberately exit 0: a deferred bake is not a failure, and the clips are
  # still valid — they are only stale. Persistent=true on the timer means the
  # next window picks it up.
  log "load ${LOAD1} exceeds ${THRESHOLD} (${CORES} cores x ${LOAD_FRACTION}) — deferring"
  exit 0
fi
log "load ${LOAD1} under ${THRESHOLD} — proceeding"

# --- bake ------------------------------------------------------------------

# Credentials come from the running worker rather than being duplicated on
# disk. TODO(poindexter#937): a dedicated least-privilege `demo-recorder`
# OAuth client would be better than reusing the worker's DB access.
DATABASE_URL="$(docker exec "$WORKER_CONTAINER" printenv DATABASE_URL)"
SECRET_KEY="$(docker exec "$WORKER_CONTAINER" printenv POINDEXTER_SECRET_KEY)"

# --out is passed EXPLICITLY rather than relying on the CLI's default. A
# scheduled job must not depend on an implicit default agreeing with the
# renderer's setting: those two drifted once already (#2922 — the CLI wrote to
# /tmp while the renderer read demo_clip_dir, so every clip landed where
# nothing looked and nothing reported a problem). Being explicit here also
# makes the script correct against any CLI version.
log "baking demo clips -> $CLIP_DIR"
set +e
docker run --rm \
  --name poindexter-demo-bake \
  --security-opt seccomp=unconfined \
  --network "container:${WORKER_CONTAINER}" \
  -v "${DEPLOY_ROOT}/src/cofounder_agent:/app:ro" \
  -v "${CLIP_DIR}:${CONTAINER_CLIP_DIR}" \
  -e HOME=/tmp \
  -e POINDEXTER_API_URL=http://localhost:8002 \
  -e DATABASE_URL="$DATABASE_URL" \
  -e POINDEXTER_SECRET_KEY="$SECRET_KEY" \
  --entrypoint sh "$IMAGE" \
  -c "cd /app && python -m poindexter media demos bake --out '${CONTAINER_CLIP_DIR}'"
RC=$?
set -e

# Scratch tapes the baker composes next to its output; harmless (only *.mp4
# and manifest.json are ever read) but they accumulate.
find "$CLIP_DIR" -maxdepth 1 -name '*.composed.tape' -delete 2>/dev/null || true

if [ $RC -ne 0 ]; then
  log "bake FAILED (rc=$RC) — previous clips are left in place"
  exit 1
fi

log "bake complete: $(find "$CLIP_DIR" -maxdepth 1 -name '*.mp4' | wc -l) clip(s)"
