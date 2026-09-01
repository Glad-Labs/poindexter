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
#      SYNC_FLOW_WAIT_MAX_SEC (default 90s) for a gap. Still busy after that:
#      DEFER — write status deferred-active-flow and exit 0; the timer's next
#      tick retries (poindexter#964 — the container bounce in step 7 kills the
#      in-flight run: a media render's nodes routinely exceed any sane gap
#      window, and each kill costs 20-40 GPU-minutes plus a wedged dispatch
#      claim). --force-flow-reset / SYNC_FLOW_FORCE=1 restores the old
#      force-through behavior for genuinely stuck queues.
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
#      for code-only merges. Attempted TWICE: recreating a service that others
#      `depends_on: service_healthy` can lose a race against its own recreate
#      ("No such container: <id>"), which strands the dependents CREATED and
#      never started. That failure also never stamps the new config-hash, so
#      the next pass re-runs the same recreate and loses the same race — a
#      10-minute loop that kept worker+grafana down (2026-08-27). The second
#      attempt sees the dependency already recreated and healthy.
#   6b. stranded sweep: start any project container left in `created` state.
#      `created` = never started, which is only ever an interrupted recreate —
#      a deliberately-stopped service (parked voice-agent) is `exited`, so this
#      cannot resurrect one. Runs even on a clean apply, and is reported in the
#      status file, because a silent self-heal hides a recurring fault.
#   7. bounce-on-change: restart the long-lived bind-mount app containers
#      (worker, pipeline-bot) so changed Python is re-imported. prefect-worker
#      is deliberately NOT bounced (each flow run is a fresh subprocess that
#      re-imports /app; a bounce would kill an in-flight post). Redundancy
#      guard: skip a container whose process already started after this
#      pass's reset (it's necessarily on the new tree).
#   8. claude.ai-connector sync: poindexter-mcp-http.service is host systemd,
#      not compose — it runs mcp-server/http_server.py out of THIS clone
#      (unit template: infrastructure/systemd/poindexter-mcp-http.service).
#      When the diff touches mcp-server/**, restart the unit; when it touches
#      mcp-server/{pyproject.toml,uv.lock} — or the clone's mcp-server/.venv
#      is missing (first pass after setup) — `uv sync` the venv first, since
#      ExecStart uses .venv/bin/python directly and the venv never
#      self-updates. .venv/ is gitignored, so step 4's reset+clean spare it.
#      Unit management needs root: plain systemctl as root, else
#      `sudo -n systemctl` (docker-watchdog precedent — the operator user
#      needs passwordless sudo; see the unit header). Hosts without the unit
#      installed skip this step; --no-restart leaves the unit alone too.
#   9. step independence: rebuilds, compose-apply, the stranded sweep,
#      restarts, and the connector sync ALL run even if an earlier one failed;
#      ANY failure withholds the marker so the pass retries next cycle
#      (re-restarting a current container is a no-op).
#
# Marker  : ~/.poindexter/deploy-last-restarted-sha   (outside the clone)
# Log     : ~/.poindexter/deploy-checkout-sync.log    (single .1 rotation)
# Status  : ~/.poindexter/deploy-checkout-sync.status.json
#   result: deployed | synced-no-change | synced-norestart | baseline-recorded
#           | deferred-active-flow | error
#
# Flags: --status (read-only health view) | --no-restart | --no-flow-check
#        | --force-flow-reset
set -uo pipefail

DEPLOY_DIR="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
SOURCE_REMOTE="${SOURCE_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"
PREFECT_API_URL="${PREFECT_API_URL:-http://localhost:4200/api}"
MAX_WAIT_SEC="${SYNC_FLOW_WAIT_MAX_SEC:-90}"
# When a flow is STILL running after MAX_WAIT_SEC: 0 (default) = defer the
# deploy (exit 0; the timer's next tick retries — a media render's nodes
# routinely exceed any sane gap window, and a kill costs 20-40 GPU-minutes).
# 1 = legacy behavior: force the reset+restarts anyway. Also --force-flow-reset.
FORCE_FLOW_RESET="${SYNC_FLOW_FORCE:-0}"
RESTART_CONTAINERS=(${SYNC_RESTART_CONTAINERS:-poindexter-worker poindexter-pipeline-bot})
SKEW_MARGIN_SEC=5
# Pause between the two compose-apply attempts (step 6). Long enough for a
# just-recreated dependency to pass its healthcheck — the shortest interval in
# the compose file is 10s — so the retry sees a settled stack instead of losing
# the same race again.
APPLY_RETRY_SETTLE_SEC="${SYNC_APPLY_RETRY_SETTLE_SEC:-15}"
# Host systemd unit serving mcp-server/http_server.py from this clone (step 8).
# "Not installed" is the opt-out — hosts that never enabled the connector skip
# the step without config. SYNC_UV_BIN overrides uv discovery (systemd PATH
# doesn't include ~/.local/bin, so we probe the standard install dirs).
MCP_UNIT="${SYNC_MCP_UNIT:-poindexter-mcp-http.service}"

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
    --force-flow-reset) FORCE_FLOW_RESET=1 ;;
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

# Media renders are NOT Prefect flows — they run inside the worker's scheduler
# job and hold the Postgres GPU advisory lock for their whole duration
# (poindexter#964: a take was killed while the Prefect queue happened to be
# idle, sailing straight past flow_running). Any granted advisory lock counts
# as GPU work in flight. Fail-open: an unreachable DB must never block deploys.
gpu_work_running() {
  local n
  n="$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tA \
        -c "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted" 2>/dev/null \
      | tr -d '[:space:]')" || return 1
  [ "${n:-0}" -gt 0 ]
}

# A media render spends most of its wall-clock in phases that hold NEITHER
# signal above: TTS narration, ASR transcription, caption alignment and the
# ffmpeg composite are CPU work inside the worker's scheduler job, so there is
# no Prefect flow and no GPU advisory lock to see. A deploy landing in that
# window restarted the worker and killed a 35-minute render on 2026-08-08
# 04:32, right after #3079 was meant to have closed exactly this. The
# render's own claim is the reliable marker: dispatched, no video asset yet.
# Bounded by SYNC_MEDIA_CLAIM_MAX_MIN so a wedged claim cannot block deploys
# forever (media_reconciliation reaps those separately).
MEDIA_CLAIM_MAX_MIN="${SYNC_MEDIA_CLAIM_MAX_MIN:-90}"

media_render_running() {
  local n
  n="$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tA \
        -c "SELECT count(*) FROM pipeline_tasks pt
             WHERE pt.media_pipeline_dispatched_at > NOW() - INTERVAL '${MEDIA_CLAIM_MAX_MIN} minutes'
               AND NOT EXISTS (
                     SELECT 1 FROM media_assets ma
                      WHERE ma.task_id = pt.task_id AND ma.type = 'video')" 2>/dev/null \
      | tr -d '[:space:]')" || return 1
  [ "${n:-0}" -gt 0 ]
}

stack_busy() { flow_running || gpu_work_running || media_render_running; }

reset_at_epoch=""
if [ "$need_reset" = "1" ]; then
  if [ "$NO_FLOW_CHECK" = "0" ]; then
    waited=0
    while [ "$waited" -lt "$MAX_WAIT_SEC" ] && stack_busy; do
      log "Active flow run, GPU job, or media render; waiting for a gap before reset (${waited}/${MAX_WAIT_SEC}s)..."
      sleep 5; waited=$((waited + 5))
    done
    if [ "$waited" -ge "$MAX_WAIT_SEC" ] && stack_busy; then
      if [ "$FORCE_FLOW_RESET" = "1" ]; then
        log "No flow gap after ${MAX_WAIT_SEC}s; forcing reset ($behind_raw commit(s) behind) — --force-flow-reset/SYNC_FLOW_FORCE set." WARN
      else
        # Killing an in-flight flow costs a full re-run (media renders:
        # 20-40 GPU-minutes) and wedges the piece's dispatch claim. Deploys
        # are idempotent and retried by the timer, so defer instead.
        log "No flow gap after ${MAX_WAIT_SEC}s; DEFERRING deploy ($behind_raw commit(s) behind) — an in-flight flow holds the slot. Retry lands on the next timer tick; pass --force-flow-reset to override." WARN
        write_status deferred-active-flow "$(git -C "$DEPLOY_DIR" rev-parse HEAD | tr -d '[:space:]')" "" "" "deferred: active flow run after ${MAX_WAIT_SEC}s wait; $behind_raw commit(s) behind"
        exit 0
      fi
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
#
# EVERY service whose Dockerfile COPYs source needs an entry here, because
# the compose-apply below runs `--no-build`: without one, a merged change to
# that source is live in the repo and DEAD in the container, with nothing
# saying so. Verified 2026-08-31 — poindexter-auto-embed was running stale
# `services/` code, and the image-gen in-flight guard (poindexter#1024) sat
# merged-but-inert until it was rebuilt by hand.
#
# `tests/unit/scripts/test_deploy_rebuild_map_coverage.py` derives the
# expected set from the Dockerfiles themselves and fails when a baked service
# has no entry, so the next sidecar cannot re-open this gap silently.
declare -A REBUILD_MAP=(
  # gpu-exporter COPYs brain/ too — found by the coverage test, not by hand.
  ['^brain/']="brain-daemon auto-embed gpu-exporter"
  ['^src/cofounder_agent/(pyproject\.toml|poetry\.lock)$|^scripts/Dockerfile\.worker$']="worker prefect-worker"
  ['^scripts/Dockerfile\.gpu-exporter$|^scripts/nvidia-smi-exporter\.py$']="gpu-exporter"
  ['^scripts/Dockerfile\.voice-agent$']="voice-agent-livekit"
  # backup-offsite/ is a SIBLING of backup/, so '^scripts/backup/' never
  # matched it — the offsite runner could change without a rebuild.
  ['^scripts/Dockerfile\.backup$|^scripts/backup/|^scripts/backup-offsite/']="backup-daily backup-hourly backup-offsite"
  # GPU sidecars — each bakes ONE server .py, which is the whole reason they
  # need a rebuild at all (the images themselves exist for torch/CUDA deps).
  ['^scripts/(Dockerfile\.image-gen|image-gen-server\.py)$']="image-gen-server"
  ['^scripts/(Dockerfile\.wan|wan-server\.py)$']="wan-server"
  ['^scripts/(Dockerfile\.stable-audio|stable-audio-server\.py)$']="stable-audio-server"
  ['^scripts/Dockerfile\.chatterbox$|^scripts/tts_sidecars/']="chatterbox"
  ['^scripts/(Dockerfile\.comfyui|comfyui-extra-model-paths\.yaml)$']="comfyui"
  # auto-embed bakes a hand-picked SUBSET of the backend tree into a minimal
  # image (short pip list, no poetry deps). It cannot bind-mount the tree the
  # worker does: adding a single COPY once let three LLM providers register
  # whose SDKs the image lacks, and every embedding store failed. So it is
  # baked on purpose — and therefore must rebuild when that subset changes.
  ['^scripts/(Dockerfile\.auto-embed|auto-embed\.py)$|^src/cofounder_agent/(poindexter|plugins|services|modules|schemas|utils)/']="auto-embed"
)
rebuild_services=""; diff_ok=0
if diff_paths="$(git -C "$DEPLOY_DIR" diff --name-only "$last_deployed" "$head_sha" 2>/dev/null)"; then
  diff_ok=1
  for re in "${!REBUILD_MAP[@]}"; do
    if echo "$diff_paths" | grep -qE "$re"; then
      rebuild_services="$rebuild_services ${REBUILD_MAP[$re]}"
    fi
  done
else
  log "Could not diff $last_short..$short_head; rebuilding brain-daemon defensively." WARN
  rebuild_services="brain-daemon"
fi

# connector change detection (diff-uncomputable -> defensive full treatment,
# same posture as the brain rebuild above)
mcp_changed=0; mcp_deps_changed=0
if [ "$diff_ok" = "1" ]; then
  echo "$diff_paths" | grep -qE '^mcp-server/' && mcp_changed=1
  echo "$diff_paths" | grep -qE '^mcp-server/(pyproject\.toml|uv\.lock)$' && mcp_deps_changed=1
else
  mcp_changed=1; mcp_deps_changed=1
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
# Retried once on failure, because the common failure here is a TRANSIENT race,
# not a bad config. When a recreated service is one that others declare
# `depends_on: <it>: service_healthy` (prometheus has 1 dependent, brain-daemon
# has 6), compose can be waiting on the pre-recreate container while that very
# container is replaced, and the wait dies with
# `dependency failed to start: ... No such container: <id>`. The dependents are
# then left CREATED-but-never-started.
#
# Left alone this self-perpetuates: the failed recreate never stamps the new
# config-hash, so the next pass tries the same recreate and loses the same race,
# every 10 minutes. Observed 2026-08-27 — worker + grafana were stranded down
# and each pass re-stranded them. A second immediate attempt converges (the
# dependency is already recreated and healthy by then), which is exactly what
# the 10-minutes-later retry was accomplishing, minus the outage in between.
apply_failed=0
for attempt in 1 2; do
  [ "$attempt" = "2" ] && log "Retrying compose-apply once (transient dependency race)..." WARN
  log "Applying compose from clone: start-stack.sh up -d --no-build (attempt $attempt/2)"
  if bash "$DEPLOY_DIR/scripts/start-stack.sh" up -d --no-build >>"$LOG_FILE" 2>&1; then
    apply_failed=0
    break
  fi
  apply_failed=1
  [ "$attempt" = "1" ] && sleep "$APPLY_RETRY_SETTLE_SEC"
done
[ "$apply_failed" = "1" ] && \
  log "compose-apply failed twice; continuing to container restarts — marker withheld, retries next cycle." ERROR

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

# ---- stranded-container sweep (safety net) --------------------------------
# A container in `created` state has NEVER been started — that state is only
# ever an interrupted recreate, never an operator's intent. A deliberately
# stopped service sits in `exited` instead, and note that those ARE still
# listed here: `poindexter-livekit` and `poindexter-voice-agent-livekit`
# survive as exited containers even with the `voice` profile out of
# compose_profiles (parked 2026-08-19, deliberately). Filtering on `created`
# is what keeps this sweep from un-parking them. That distinction is the whole
# safety argument for starting these unattended — do NOT widen the filter to
# `exited`, and do not reach for `compose start`, which would take the whole
# lot up.
#
# This runs even when compose-apply succeeded, and independently of the restart
# loop above: the loop only covers RESTART_CONTAINERS, and `docker restart` can
# itself race a recreate that is still in flight (2026-08-27: the loop logged
# "restarted 'poindexter-worker'" while the container it had just been handed
# was replaced underneath it — the worker stayed down for the rest of the
# cycle). The project is resolved through start-stack.sh so this uses the same
# COMPOSE_PROJECT_NAME the apply did, rather than a second copy of the
# bootstrap parsing that could drift from it.
stranded_failed=0; stranded_started=""
while IFS= read -r c; do
  [ -z "$c" ] && continue
  log "  stranded '$c' (created, never started) — starting" WARN
  if docker start "$c" >>"$LOG_FILE" 2>&1; then
    stranded_started="${stranded_started:+$stranded_started,}$c"
  else
    stranded_failed=1; log "  FAILED to start stranded '$c'" ERROR
  fi
done < <(bash "$DEPLOY_DIR/scripts/start-stack.sh" ps --status=created --format '{{.Name}}' 2>/dev/null || true)
[ -n "$stranded_started" ] && log "Recovered stranded containers: $stranded_started" WARN

# ---- claude.ai-connector sync (host systemd unit, not compose) -------------
# poindexter-mcp-http.service runs mcp-server/http_server.py out of THIS
# clone; before 2026-08-16 it ran from the operator checkout and mcp-server
# merges silently never reached the phone surface (PR #3247 needed a manual
# FF + restart). ExecStart is .venv/bin/python (not `uv run`), so dependency
# changes need an explicit `uv sync`; a missing venv (first pass after
# setup-deploy-checkout.sh) is self-healed the same way. .venv/ is
# gitignored, so the reset+clean above spare it.
mcp_failed=0; mcp_venv_python="$DEPLOY_DIR/mcp-server/.venv/bin/python"

mcp_unit_loaded() {
  command -v systemctl >/dev/null 2>&1 || return 1
  [ "$(systemctl show -p LoadState --value "$MCP_UNIT" 2>/dev/null)" = "loaded" ]
}

systemctl_root() { # unit management needs root; queries above do not
  if [ "$(id -u)" = "0" ]; then systemctl "$@"; else sudo -n systemctl "$@"; fi
}

if mcp_unit_loaded; then
  need_uv_sync=0; need_mcp_restart=0
  [ "$mcp_deps_changed" = "1" ] && need_uv_sync=1
  [ -x "$mcp_venv_python" ] || need_uv_sync=1   # self-heal a missing venv
  [ "$mcp_changed" = "1" ] && need_mcp_restart=1
  [ "$need_uv_sync" = "1" ] && need_mcp_restart=1  # fresh deps => reload process

  if [ "$need_uv_sync" = "1" ]; then
    UV_BIN="${SYNC_UV_BIN:-$(command -v uv || true)}"
    if [ -z "$UV_BIN" ]; then
      for cand in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        [ -x "$cand" ] && { UV_BIN="$cand"; break; }
      done
    fi
    if [ -z "$UV_BIN" ]; then
      log "connector: uv not found but $DEPLOY_DIR/mcp-server needs a venv sync; install uv or set SYNC_UV_BIN." ERROR
      mcp_failed=1
    elif "$UV_BIN" sync --directory "$DEPLOY_DIR/mcp-server" >>"$LOG_FILE" 2>&1; then
      log "connector: mcp-server venv synced"
    else
      log "connector: uv sync failed for $DEPLOY_DIR/mcp-server" ERROR
      mcp_failed=1
    fi
  fi

  if [ "$mcp_failed" = "0" ] && [ "$need_mcp_restart" = "1" ]; then
    if systemctl_root restart "$MCP_UNIT" >>"$LOG_FILE" 2>&1; then
      restarted="${restarted:+$restarted,}$MCP_UNIT"; log "connector: restarted $MCP_UNIT"
    else
      log "connector: FAILED to restart $MCP_UNIT (root or passwordless sudo for systemctl required — see the unit header)" ERROR
      mcp_failed=1
    fi
  fi
elif [ "$mcp_changed" = "1" ]; then
  log "connector: mcp-server/ changed but $MCP_UNIT is not installed on this host; skipping."
fi

# ---- outcome (step independence: marker only on a fully-clean pass) --------
if [ "$build_failed" = "0" ] && [ "$apply_failed" = "0" ] && [ "$restart_failed" = "0" ] && [ "$stranded_failed" = "0" ] && [ "$mcp_failed" = "0" ]; then
  printf '%s' "$head_sha" > "$MARKER_FILE"
  detail=""
  [ -n "$rebuild_services" ] && detail="rebuilt: $rebuild_services"
  [ -n "$skipped" ] && detail="${detail:+$detail; }skipped already-fresh: $skipped"
  # Surface the recovery in the status file even on a clean pass — a sweep that
  # had to act means the apply raced, and a silent self-heal is how a recurring
  # fault stays invisible.
  [ -n "$stranded_started" ] && detail="${detail:+$detail; }recovered stranded: $stranded_started"
  log "Pipeline now running $short_head. ${detail}"
  write_status deployed "$head_sha" "$last_deployed" "$restarted" "$detail"
else
  steps=""
  [ "$build_failed" = "1" ] && steps="${steps}image-rebuild "
  [ "$apply_failed" = "1" ] && steps="${steps}compose-apply "
  [ "$restart_failed" = "1" ] && steps="${steps}container-restart "
  [ "$stranded_failed" = "1" ] && steps="${steps}stranded-start "
  [ "$mcp_failed" = "1" ] && steps="${steps}mcp-connector "
  log "Deploy pass incomplete (failed: $steps); NOT recording marker — retries next cycle." ERROR
  write_status error "$head_sha" "$last_deployed" "$restarted" "failed steps: $steps"
  exit 1
fi
