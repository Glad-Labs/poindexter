#!/usr/bin/env bash
# deploy-checkout-sync.sh — keep the dedicated deploy checkout on origin/main
# and make merged code actually RUN. Linux port of scripts/deploy-checkout-sync.ps1
# (poindexter#228; see that file's header for the full design history — the
# Prefect starvation fix, the 2026-07-02 step-independence bug, the redundancy
# guard). Runs every 10 min via infrastructure/systemd/poindexter-deploy-sync.timer.
#
# One pass:
#   1. git fetch (always safe — never touches the working tree)
#   2. behind-check: 0 commits behind -> no-op (fail-safe parse: junk = behind)
#   3. Prefect flow-gap guard: prefer resetting between flow runs; wait up to
#      SYNC_FLOW_WAIT_MAX_SEC (default 90s) for a gap, then FORCE the reset —
#      a long-running flow already imported its modules, so a mid-run reset is
#      safe for it; only a subprocess *starting* mid-reset can read a torn tree
#      (fails once, reclaimed + retried). Never starve the deploy.
#   4. git reset --hard origin/main + git clean -fd   (SAFE: dedicated clone,
#      nothing else ever edits it — never point this at a working checkout)
#   5. rebuild map: diff (last-deployed..HEAD) -> image-baked services whose
#      build inputs changed get `start-stack.sh build <svc>` (generalized from
#      the ps1's brain-only rebuild — restarts/rebuilds are safe by design):
#        brain/**                                   -> brain-daemon
#        src/cofounder_agent/pyproject.toml|poetry.lock
#          |scripts/Dockerfile.worker               -> worker prefect-worker
#        scripts/Dockerfile.gpu-exporter
#          |scripts/nvidia-smi-exporter.py          -> gpu-exporter
#          (the .py is bind-mounted, so a restart would suffice to reload it —
#           but rebuild+recreate is deliberate: recreation is the ONLY thing
#           that re-injects NVIDIA device nodes, which bind at container-create
#           time. A card added after the container was created is otherwise
#           invisible forever; that hid an RTX 3090 for 7+ days, 2026-07-26.)
#        scripts/Dockerfile.voice-agent             -> voice-agent-livekit
#        scripts/Dockerfile.backup|scripts/backup/**-> backup-daily backup-hourly backup-offsite
#      (diff-uncomputable -> defensive brain-daemon rebuild, same as the ps1)
#   6. compose-apply: clone's `start-stack.sh up -d --no-build` — recreates
#      services whose compose stanza OR freshly-built image changed; no-op
#      for code-only merges
#   7. bounce-on-change: restart the long-lived bind-mount app containers
#      (worker, pipeline-bot) so changed Python is re-imported. prefect-worker
#      is deliberately NOT bounced (each flow run is a fresh subprocess that
#      re-imports /app; a bounce would kill an in-flight post). Redundancy
#      guard: skip a container whose process already started after this
#      pass's reset (it's necessarily on the new tree).
#   8. step independence: rebuilds, compose-apply, and restarts ALL run even
#      if an earlier one failed; ANY failure withholds the marker so the pass
#      retries next cycle (re-restarting a current container is a no-op).
#
# Marker  : ~/.poindexter/deploy-last-restarted-sha   (outside the clone)
# Log     : ~/.poindexter/deploy-checkout-sync.log    (single .1 rotation)
# Status  : ~/.poindexter/deploy-checkout-sync.status.json
#   result: deployed | synced-no-change | synced-norestart | baseline-recorded | error
#
# Flags: --status (read-only health view) | --no-restart | --no-flow-check
set -uo pipefail

DEPLOY_DIR="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
SOURCE_REMOTE="${SOURCE_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"
PREFECT_API_URL="${PREFECT_API_URL:-http://localhost:4200/api}"
MAX_WAIT_SEC="${SYNC_FLOW_WAIT_MAX_SEC:-90}"
RESTART_CONTAINERS=(${SYNC_RESTART_CONTAINERS:-poindexter-worker poindexter-pipeline-bot})
SKEW_MARGIN_SEC=5

POINDEXTER_HOME="$HOME/.poindexter"
LOG_FILE="$POINDEXTER_HOME/deploy-checkout-sync.log"
STATUS_FILE="$POINDEXTER_HOME/deploy-checkout-sync.status.json"
MARKER_FILE="$POINDEXTER_HOME/deploy-last-restarted-sha"
LOG_MAX_BYTES="${POINDEXTER_DEPLOY_LOG_MAX_BYTES:-5242880}"

NO_RESTART=0; NO_FLOW_CHECK=0
for arg in "$@"; do
  case "$arg" in
    --status)
      echo "deploy clone: $DEPLOY_DIR"
      git -C "$DEPLOY_DIR" rev-parse --short HEAD 2>/dev/null | sed 's/^/  HEAD: /' || echo "  (clone missing)"
      [ -f "$STATUS_FILE" ] && { echo "  --- last status ---"; cat "$STATUS_FILE"; echo; }
      [ -f "$LOG_FILE" ] && { echo "  --- last 15 log lines ---"; tail -15 "$LOG_FILE"; }
      exit 0 ;;
    --no-restart) NO_RESTART=1 ;;
    --no-flow-check) NO_FLOW_CHECK=1 ;;
  esac
done

log() { # log <msg> [LEVEL]
  local line; line="$(date '+%Y-%m-%d %H:%M:%S') [${2:-INFO}] $1"
  echo "[deploy-checkout-sync] $1"
  echo "$line" >> "$LOG_FILE" 2>/dev/null || true
}

write_status() { # write_status <result> <head> <prev> <restarted-csv> <detail>
  printf '{"timestamp":"%s","result":"%s","head":"%s","previousHead":"%s","restarted":[%s],"detail":"%s","host":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" \
    "$(echo "${4:-}" | sed 's/[^,]\+/"&"/g')" \
    "$(echo "${5:-}" | tr '"' "'")" "$(hostname)" > "$STATUS_FILE" 2>/dev/null || true
}

# single-backup rotation
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)" -ge "$LOG_MAX_BYTES" ]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null || true
fi

if ! git -C "$DEPLOY_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "ERROR: $DEPLOY_DIR is not a git checkout. Run scripts/setup-deploy-checkout.sh first." ERROR
  write_status error "" "" "" "deploy dir is not a git checkout"
  exit 1
fi

log "Syncing $DEPLOY_DIR to $SOURCE_REMOTE/$SYNC_BRANCH ..."
if ! git -C "$DEPLOY_DIR" fetch "$SOURCE_REMOTE" "$SYNC_BRANCH" --prune >>"$LOG_FILE" 2>&1; then
  log "fetch failed" ERROR; write_status error "" "" "" "git fetch failed"; exit 1
fi

# behind-check — fail-safe: unparseable output counts as behind
behind_raw="$(git -C "$DEPLOY_DIR" rev-list --count "HEAD..$SOURCE_REMOTE/$SYNC_BRANCH" 2>/dev/null | tr -d '[:space:]')"
if [[ "$behind_raw" =~ ^[0-9]+$ ]]; then need_reset=$(( behind_raw > 0 )); else need_reset=1; fi

flow_running() {
  local n
  n="$(curl -sf --max-time 3 -X POST "$PREFECT_API_URL/flow_runs/filter" \
        -H 'Content-Type: application/json' \
        -d '{"flow_runs":{"state":{"type":{"any_":["RUNNING"]}}},"limit":1}' 2>/dev/null \
      | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))' 2>/dev/null)" || return 1
  [ "${n:-0}" -gt 0 ]
}

reset_at_epoch=""
if [ "$need_reset" = "1" ]; then
  if [ "$NO_FLOW_CHECK" = "0" ]; then
    waited=0
    while [ "$waited" -lt "$MAX_WAIT_SEC" ] && flow_running; do
      log "Active Prefect flow run; waiting for a gap before reset (${waited}/${MAX_WAIT_SEC}s)..."
      sleep 5; waited=$((waited + 5))
    done
    if [ "$waited" -ge "$MAX_WAIT_SEC" ] && flow_running; then
      log "No flow gap after ${MAX_WAIT_SEC}s; forcing reset ($behind_raw commit(s) behind) to avoid deploy starvation." WARN
    fi
  fi
  if ! git -C "$DEPLOY_DIR" reset --hard "$SOURCE_REMOTE/$SYNC_BRANCH" >>"$LOG_FILE" 2>&1; then
    log "reset failed" ERROR; write_status error "" "" "" "git reset --hard failed"; exit 1
  fi
  git -C "$DEPLOY_DIR" clean -fd >>"$LOG_FILE" 2>&1 || true
  reset_at_epoch="$(date -u +%s)"
else
  log "Already at $SOURCE_REMOTE/$SYNC_BRANCH (0 commits behind); no reset needed."
fi

head_sha="$(git -C "$DEPLOY_DIR" rev-parse HEAD | tr -d '[:space:]')"
short_head="${head_sha:0:9}"
log "Deploy checkout now at $short_head ($SOURCE_REMOTE/$SYNC_BRANCH)."

if [ "$NO_RESTART" = "1" ]; then
  log "--no-restart set; code synced on disk, containers left as-is."
  write_status synced-norestart "$head_sha" "" "" ""
  exit 0
fi

last_deployed="$(cat "$MARKER_FILE" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$last_deployed" ]; then
  printf '%s' "$head_sha" > "$MARKER_FILE"
  log "No prior deploy marker; recorded baseline $short_head without restarting."
  write_status baseline-recorded "$head_sha" "" "" ""
  exit 0
fi
if [ "$last_deployed" = "$head_sha" ]; then
  log "Containers already on $short_head; nothing to restart."
  write_status synced-no-change "$head_sha" "" "" ""
  exit 0
fi

last_short="${last_deployed:0:9}"
log "Code advanced $last_short -> $short_head; deploying."

# ---- rebuild map (generalized from the ps1's brain-only rebuild) ----------
# path-regex -> compose services (image-baked build inputs). Restarts and
# rebuilds are safe by design; when in doubt about the diff, rebuild brain
# defensively like the ps1 did.
declare -A REBUILD_MAP=(
  ['^brain/']="brain-daemon"
  ['^src/cofounder_agent/(pyproject\.toml|poetry\.lock)$|^scripts/Dockerfile\.worker$']="worker prefect-worker"
  ['^scripts/Dockerfile\.gpu-exporter$|^scripts/nvidia-smi-exporter\.py$']="gpu-exporter"
  ['^scripts/Dockerfile\.voice-agent$']="voice-agent-livekit"
  ['^scripts/Dockerfile\.backup$|^scripts/backup/']="backup-daily backup-hourly backup-offsite"
)
rebuild_services=""
if diff_paths="$(git -C "$DEPLOY_DIR" diff --name-only "$last_deployed" "$head_sha" 2>/dev/null)"; then
  for re in "${!REBUILD_MAP[@]}"; do
    if echo "$diff_paths" | grep -qE "$re"; then
      rebuild_services="$rebuild_services ${REBUILD_MAP[$re]}"
    fi
  done
else
  log "Could not diff $last_short..$short_head; rebuilding brain-daemon defensively." WARN
  rebuild_services="brain-daemon"
fi
rebuild_services="$(echo "$rebuild_services" | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ' | sed 's/ $//')"

build_failed=0
if [ -n "$rebuild_services" ]; then
  log "Build inputs changed in $last_short..$short_head; rebuilding: $rebuild_services"
  # shellcheck disable=SC2086 — deliberate word-split of the service list
  if ! bash "$DEPLOY_DIR/scripts/start-stack.sh" build $rebuild_services >>"$LOG_FILE" 2>&1; then
    log "image rebuild failed ($rebuild_services); continuing — marker withheld, retries next cycle." ERROR
    build_failed=1
  fi
fi

# ---- compose-apply (recreates changed-stanza / freshly-built services) ----
apply_failed=0
log "Applying compose from clone: start-stack.sh up -d --no-build"
if ! bash "$DEPLOY_DIR/scripts/start-stack.sh" up -d --no-build >>"$LOG_FILE" 2>&1; then
  log "compose-apply failed; continuing to container restarts — marker withheld, retries next cycle." ERROR
  apply_failed=1
fi

# ---- bounce-on-change with redundancy guard --------------------------------
restart_failed=0; restarted=""; skipped=""
for c in "${RESTART_CONTAINERS[@]}"; do
  if ! docker container inspect "$c" >/dev/null 2>&1; then
    log "  skip '$c' (not present)"; continue
  fi
  if [ -n "$reset_at_epoch" ]; then
    started_at="$(docker container inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null)"
    started_epoch="$(date -u -d "$started_at" +%s 2>/dev/null || echo "")"
    if [ -n "$started_epoch" ] && [ "$started_epoch" -gt $((reset_at_epoch + SKEW_MARGIN_SEC)) ]; then
      skipped="$skipped$c "; log "  skip '$c' (already restarted onto this tree at $started_at)"; continue
    fi
  fi
  if docker restart "$c" >>"$LOG_FILE" 2>&1; then
    restarted="${restarted:+$restarted,}$c"; log "  restarted '$c'"
  else
    restart_failed=1; log "  FAILED to restart '$c'" ERROR
  fi
done

# ---- outcome (step independence: marker only on a fully-clean pass) --------
if [ "$build_failed" = "0" ] && [ "$apply_failed" = "0" ] && [ "$restart_failed" = "0" ]; then
  printf '%s' "$head_sha" > "$MARKER_FILE"
  detail=""
  [ -n "$rebuild_services" ] && detail="rebuilt: $rebuild_services"
  [ -n "$skipped" ] && detail="${detail:+$detail; }skipped already-fresh: $skipped"
  log "Pipeline now running $short_head. ${detail}"
  write_status deployed "$head_sha" "$last_deployed" "$restarted" "$detail"
else
  steps=""
  [ "$build_failed" = "1" ] && steps="${steps}image-rebuild "
  [ "$apply_failed" = "1" ] && steps="${steps}compose-apply "
  [ "$restart_failed" = "1" ] && steps="${steps}container-restart "
  log "Deploy pass incomplete (failed: $steps); NOT recording marker — retries next cycle." ERROR
  write_status error "$head_sha" "$last_deployed" "$restarted" "failed steps: $steps"
  exit 1
fi
