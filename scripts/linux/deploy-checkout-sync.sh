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
#   3. Prefect flow-gap guard (wait_for_gap_or_defer): prefer resetting between flow runs; wait up to
#      SYNC_FLOW_WAIT_MAX_SEC (default 90s) for a gap. Still busy after that:
#      DEFER — write status deferred-active-flow and exit 0; the timer's next
#      tick retries (poindexter#964 — the container bounce in step 7 kills the
#      in-flight run: a media render's nodes routinely exceed any sane gap
#      window, and each kill costs 20-40 GPU-minutes plus a wedged dispatch
#      claim). --force-flow-reset / SYNC_FLOW_FORCE=1 restores the old
#      force-through behavior for genuinely stuck queues. The same guard runs
#      again before any rebuild/restart on a pass that did NOT reset (clone
#      already current) — poindexter#1068: that path used to bounce the worker
#      with no busy check at all.
#   4. git reset --hard origin/main + git clean -fd   (SAFE: dedicated clone,
#      nothing else ever edits it — never point this at a working checkout)
#   5. rebuild map: diff (last-deployed..HEAD) -> image-baked services whose
#      build inputs changed get `start-stack.sh build <svc>` (generalized from
#      the ps1's brain-only rebuild — restarts/rebuilds are safe by design):
#        src/cofounder_agent/poindexter/brain/**    -> brain-daemon
#        src/cofounder_agent/pyproject.toml|poetry.lock
#          |src/cofounder_agent/Dockerfile.worker   -> worker prefect-worker pipeline-bot demo-recorder
#        scripts/Dockerfile.gpu-exporter
#          |scripts/nvidia-smi-exporter.py          -> gpu-exporter
#          (the .py is bind-mounted, so a restart would suffice to reload it —
#           but rebuild+recreate is deliberate: recreation is the ONLY thing
#           that re-injects NVIDIA device nodes, which bind at container-create
#           time. A card added after the container was created is otherwise
#           invisible forever; that hid an RTX 3090 for 7+ days, 2026-07-26.)
#        scripts/Dockerfile.voice-agent             -> voice-agent-livekit voice-agent-claude-code
#        scripts/Dockerfile.backup|scripts/backup/**-> backup-daily backup-hourly backup-offsite
#      (diff-uncomputable -> defensive brain-daemon rebuild, same as the ps1)
#      Building never starts anything: a rebuilt service that is not running
#      (a parked, profile-gated one) is built and left stopped — see 6a-bis.
#   6. compose-apply: clone's `start-stack.sh up -d --no-build` — recreates
#      services whose compose STANZA changed, AND every service whose freshly
#      built image has different CONTENT: compose compares the platform
#      manifest digest it recorded on the container (`com.docker.compose.image`)
#      with the one the tag now names. A rebuild that changed nothing mints a
#      new image ID but the same manifest, and compose rightly leaves that
#      container running. Verified 2026-09-28 on a throwaway project (dry-run
#      and real run agree), and in this log: across all 46 passes with a
#      rebuild from 09-23 to 09-28, every service compose left alone was a
#      failed or no-op build. The 09-22 belief that compose never recreates a
#      same-tag rebuild was a false positive from comparing image IDs (see
#      6a-bis). Attempted TWICE: recreating a service that others
#      `depends_on: service_healthy` can lose a race against its own recreate
#      ("No such container: <id>"), which strands the dependents CREATED and
#      never started. That failure also never stamps the new config-hash, so
#      the next pass re-runs the same recreate and loses the same race — a
#      10-minute loop that kept worker+grafana down (2026-08-27). The second
#      attempt sees the dependency already recreated and healthy.
#   6a-bis. recreate check (only after a successful compose-apply): for each
#      service this pass rebuilt, compare the platform manifest its container
#      runs with the one its image ref names (`deploy_health_gate.py
#      recreate-plan`) and force-recreate only the ones compose-apply left on
#      the previous image. Normally none: this is the check that compose did
#      its job, and the repair if it did not. Until 2026-09-28 it
#      force-recreated EVERY rebuilt service, so each one compose had just
#      recreated started twice (16 of 48 brain starts in a week came 72-138 s
#      after the one before — an ~80 s monitoring gap each, which also reset
#      the brain's in-memory failure counts and alert cooldowns). Only LIVE
#      services are ever named, recreated or gated: those running before this
#      pass touched them, or running after compose-apply. A rebuilt service
#      that is neither is parked (voice, demo-recorder): naming it in
#      `up --force-recreate` enables its profile and starts it, and gating it
#      would read `exited` as a broken image whose rollback starts it too.
#      That holds even when the plan cannot run or compose-apply failed.
#      Anything live the plan cannot compare is recreated; a failed recreate,
#      or a state that cannot be read, withholds the marker.
#   6b. stranded sweep: start any project container left in `created` state.
#      `created` = never started, which is only ever an interrupted recreate —
#      a deliberately-stopped service (parked voice-agent) is `exited`, so this
#      cannot resurrect one. Runs even on a clean apply, and is reported in the
#      status file, because a silent self-heal hides a recurring fault.
#   6b. health gate (scripts/linux/deploy_health_gate.py, 2026-09-13): every
#      live rebuilt service (6a-bis) is watched until it is healthy — a parked
#      one is not, since its rollback would start it; a container that comes up
#      `restarting`/`exited`/`unhealthy` is ROLLED BACK onto the image it ran
#      before the rebuild (snapshot taken before `build`), a critical alert
#      carries its last log lines, and the sha is recorded in
#      deploy-rolled-back-sha so the same broken build is not retried every
#      10 minutes — the fix must merge as a new commit. Bounced bind-mount
#      containers are watched too (page only; their rollback is a code revert).
#      Chatterbox restarted 507 times behind "Pipeline now running …" before
#      this existed. Settings: deploy_health_gate_seconds,
#      deploy_health_gate_settle_seconds, deploy_rollback_on_unhealthy;
#      --no-gate skips it.
#   6c. game-mode re-park (2026-09-15): `up -d` starts EVERY project service,
#      including the GPU sidecars the operator parked with `poindexter game on`
#      (they sit `exited`, which compose reads as "start me"). The brain
#      re-parks them on its next cycle, but that is up to five minutes of
#      chatterbox / image-gen / speaches / stable-audio / wan-server warming
#      back onto the card mid-game. This step reads the same app_settings keys
#      the brain and CLI read and stops what the apply woke. It runs AFTER the
#      health gate on purpose: a rebuilt sidecar must come up healthy before it
#      is parked again, or the gate would roll back a build for being parked.
#   7. bounce-on-change: restart the long-lived bind-mount app containers
#      (worker, pipeline-bot) so changed Python is re-imported. prefect-worker
#      is deliberately NOT bounced (each flow run is a fresh subprocess that
#      re-imports /app; a bounce would kill an in-flight post). Two guards:
#      skip a container whose process already started after the clone reached
#      this tree, since it is necessarily on it ("reached" = this pass's reset,
#      or, when the clone was already current, HEAD's last move in git's
#      reflog); and -- ONCE PER TREE -- skip the whole
#      bounce when ~/.poindexter/deploy-last-bounced-sha already names HEAD and
#      this pass did no reset. That file is written as soon as the restarts
#      succeed, independently of the deploy marker below, so a pass that is
#      retrying some OTHER failed step (image-rebuild, compose-apply) does not
#      restart the worker again. Before stack#3661 (2026-09-11) it did: a broken
#      sidecar build withheld the marker for 12 h and every 10-minute retry
#      bounced the worker, ~70 restarts, each killing in-flight scheduler jobs.
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
#      ANY failure withholds the marker so the pass retries next cycle. A retry
#      redoes only what did not complete: the bounce and the connector restart
#      remember the tree they last finished for (see 7 and 8) and are skipped
#      while HEAD is unchanged -- restarting a healthy container is NOT a no-op.
#  10. host CLI environment (stack#4156): the host `poindexter` command runs out
#      of ~/.poindexter/cli-venv, whose package is editable-installed from THIS
#      clone (scripts/linux/cli-venv-sync.sh, poindexter-cli.sh). Code needs
#      nothing, because each CLI call is a fresh process. A poetry.lock change
#      does have to reach the venv, so every pass that reaches a deploy decision
#      runs the clone's cli-venv-sync.sh (all but a deferral or a fetch/reset
#      failure), no-change passes included: that is how a failed sync heals.
#      On a deploy pass it runs LAST, after the marker and status are written.
#      A lockfile change rebuilds the worker images AND needs this sync, and
#      the unit kills the whole pass at TimeoutStartSec, so running it any
#      earlier could cost the marker and trigger a second round of rebuilds and
#      force-recreates. Its timeout, SYNC_CLI_VENV_TIMEOUT_SEC (300s), plus the
#      longest pass seen (~400s) stays inside the unit's 900s. A failure never
#      withholds the marker: PyPI being unreachable is not a failed container
#      deploy. It amends the status detail instead, and the launcher retries on
#      the operator's next command. A host that never installed the host CLI
#      has no venv: the script exits 3 and the step is silent.
#
# Marker  : ~/.poindexter/deploy-last-restarted-sha   (outside the clone; a fully clean pass)
#           ~/.poindexter/deploy-last-bounced-sha     (tree the app containers were last restarted onto)
#           ~/.poindexter/deploy-last-connector-sha   (tree the connector step last completed for)
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
# Host CLI environment (step 10). The script is read from the CLONE, like the
# health gate, so its logic deploys itself; only these call sites ride the
# operator checkout. CLI_ENV_NOTE carries a failure into write_status.
CLI_VENV_SYNC="$DEPLOY_DIR/scripts/linux/cli-venv-sync.sh"
CLI_VENV_TIMEOUT_SEC="${SYNC_CLI_VENV_TIMEOUT_SEC:-300}"
CLI_ENV_NOTE=""
STATUS_AMEND=0

POINDEXTER_HOME="$HOME/.poindexter"
LOG_FILE="$POINDEXTER_HOME/deploy-checkout-sync.log"
STATUS_FILE="$POINDEXTER_HOME/deploy-checkout-sync.status.json"
MARKER_FILE="$POINDEXTER_HOME/deploy-last-restarted-sha"
# Per-step "done for this tree" records (stack#3661): written by the bounce loop
# and the connector step the moment THEY succeed, so a pass that fails elsewhere
# and retries next cycle does not redo them. Distinct from MARKER_FILE, which
# only a fully clean pass writes and which drives the code-advanced diff.
BOUNCE_MARKER_FILE="$POINDEXTER_HOME/deploy-last-bounced-sha"
CONNECTOR_MARKER_FILE="$POINDEXTER_HOME/deploy-last-connector-sha"
ROLLBACK_MARKER_FILE="$POINDEXTER_HOME/deploy-rolled-back-sha"
GATE_SNAPSHOT_FILE="$POINDEXTER_HOME/deploy-gate-snapshot.json"
HEALTH_GATE="$DEPLOY_DIR/scripts/linux/deploy_health_gate.py"
LOG_MAX_BYTES="${POINDEXTER_DEPLOY_LOG_MAX_BYTES:-5242880}"

NO_RESTART=0; NO_FLOW_CHECK=0; NO_GATE=0
for arg in "$@"; do
  case "$arg" in
    --status)
      echo "deploy clone: $DEPLOY_DIR"
      git -C "$DEPLOY_DIR" rev-parse --short HEAD 2>/dev/null | sed 's/^/  HEAD: /' || echo "  (clone missing)"
      [ -f "$BOUNCE_MARKER_FILE" ] && echo "  containers last bounced onto: $(cut -c1-9 "$BOUNCE_MARKER_FILE")"
      [ -f "$STATUS_FILE" ] && { echo "  --- last status ---"; cat "$STATUS_FILE"; echo; }
      [ -f "$LOG_FILE" ] && { echo "  --- last 15 log lines ---"; tail -15 "$LOG_FILE"; }
      [ -f "$CLI_VENV_SYNC" ] && { echo "  --- host CLI env ---"; bash "$CLI_VENV_SYNC" --status 2>&1 | sed 's/^/  /'; }
      exit 0 ;;
    --no-restart) NO_RESTART=1 ;;
    --no-flow-check) NO_FLOW_CHECK=1 ;;
      --no-gate) NO_GATE=1 ;;
    --force-flow-reset) FORCE_FLOW_RESET=1 ;;
  esac
done

log() { # log <msg> [LEVEL]
  local line; line="$(date '+%Y-%m-%d %H:%M:%S') [${2:-INFO}] $1"
  echo "[deploy-checkout-sync] $1"
  echo "$line" >> "$LOG_FILE" 2>/dev/null || true
}

write_status() { # write_status <result> <head> <prev> <restarted-csv> <detail>
  # Step 10 never changes the result; a failure there rides along in the detail.
  local detail="${5:-}"
  [ -n "${CLI_ENV_NOTE:-}" ] && detail="${detail:+$detail; }$CLI_ENV_NOTE"
  printf '{"timestamp":"%s","result":"%s","head":"%s","previousHead":"%s","restarted":[%s],"detail":"%s","host":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" \
    "$(echo "${4:-}" | sed 's/[^,]\+/"&"/g')" \
    "$(echo "$detail" | tr '"' "'")" "$(hostname)" > "$STATUS_FILE" 2>/dev/null || true
  # One heartbeat per pass: step 10's late amendment rewrites only the file.
  [ "$STATUS_AMEND" = "1" ] || emit_run_heartbeat "$1" "$2" "$3" "${4:-}" "$detail"
}

# Mirror the status into audit_log so the deploy path has a LIVENESS signal
# something else can read (poindexter#977).
#
# The status file alone cannot serve that purpose: it lives at the
# ~/.poindexter root, and the brain container mounts only subdirectories of
# that root (backups/, deploy/, logs/). Mounting the root to expose one JSON
# file would also expose bootstrap.toml — the master key — to a container that
# has no business reading it, and a single-file bind mount goes stale when the
# writer replaces the inode. The DB is already the bus every other component
# reports through, and this script already reaches it (see gpu_work_running),
# so the heartbeat goes there.
#
# Fail-open on EVERY path: a deploy must never be blocked, delayed, or failed
# by the reporting of it. Postgres being unreachable is itself often WHY a
# deploy is failing, and a heartbeat that could hang would turn a bad minute
# into a bad hour. `docker exec` inherits no timeout, hence the explicit one.
emit_run_heartbeat() { # emit_run_heartbeat <result> <head> <prev> <restarted-csv> <detail>
  local result="$1" head="$2" detail="${5:-}"
  # `$` would terminate the $$-quoted literal below; newlines would break the
  # single-statement -c form. Same defusing the offsite backup runner does.
  detail="$(printf '%s' "${detail}" | tr '\n$' ' _' | cut -c1-500)"
  timeout 10 docker exec poindexter-postgres-local psql -U poindexter \
    -d poindexter_brain -tA -c \
    "INSERT INTO audit_log (event_type, source, details, severity)
     VALUES ('deploy_sync_run', 'deploy-checkout-sync',
             jsonb_build_object('result', \$\$${result}\$\$,
                                'head', \$\$${head}\$\$,
                                'detail', \$\$${detail}\$\$,
                                'host', \$\$$(hostname)\$\$),
             CASE WHEN \$\$${result}\$\$ = 'error' THEN 'warning' ELSE 'info' END)" \
    >/dev/null 2>&1 || true
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

# poindexter#964: the media GRAPH holds a GPU advisory lock only per-lane
# render, and media_render_running() above goes blind the moment ANY video
# asset lands for the task — a dual-lane piece (long-form then short) is
# killable at the boundary between lanes, once the long asset exists but the
# short is still rendering. Reproduced 2026-09-25 17:50:22: poindexter-worker
# was restarted while task cc260343 was 16 minutes into its SECOND lane (the
# long-form asset had already persisted, so media_render_running() read
# "not running"); that render happened to survive the restart, but the gap
# is real and was already diagnosed — unfixed — in this issue's own history.
#
# live_activity's heartbeat (kind='media') spans BOTH lanes of a piece, so a
# FRESH row here is the one signal the asset-existence check above can't see.
# Heartbeat freshness, not claim age — mirrors the liveness check
# media_reconciliation now uses for the identical shape (poindexter#1069).
MEDIA_LIVE_HEARTBEAT_MAX_MIN="${SYNC_MEDIA_LIVE_HEARTBEAT_MAX_MIN:-3}"

media_live_activity_running() {
  local n
  n="$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tA \
        -c "SELECT count(*) FROM live_activity
             WHERE kind = 'media'
               AND finished_at IS NULL
               AND updated_at > NOW() - INTERVAL '${MEDIA_LIVE_HEARTBEAT_MAX_MIN} minutes'" 2>/dev/null \
      | tr -d '[:space:]')" || return 1
  [ "${n:-0}" -gt 0 ]
}

stack_busy() { flow_running || gpu_work_running || media_render_running || media_live_activity_running; }

# Wait up to MAX_WAIT_SEC for a gap in flows / GPU work / media renders; still
# busy after that -> write deferred-active-flow and exit 0 (the next timer tick
# retries), unless --force-flow-reset. <what> names the step being held.
wait_for_gap_or_defer() { # wait_for_gap_or_defer <what>
  local what="$1" waited=0
  [ "$NO_FLOW_CHECK" = "1" ] && return 0
  while [ "$waited" -lt "$MAX_WAIT_SEC" ] && stack_busy; do
    log "Active flow run, GPU job, or media render; waiting for a gap before $what (${waited}/${MAX_WAIT_SEC}s)..."
    sleep 5; waited=$((waited + 5))
  done
  if [ "$waited" -ge "$MAX_WAIT_SEC" ] && stack_busy; then
    if [ "$FORCE_FLOW_RESET" = "1" ]; then
      log "No flow gap after ${MAX_WAIT_SEC}s; forcing $what — --force-flow-reset/SYNC_FLOW_FORCE set." WARN
    else
      # Killing an in-flight flow costs a full re-run (media renders:
      # 20-40 GPU-minutes) and wedges the piece's dispatch claim. Deploys
      # are idempotent and retried by the timer, so defer instead.
      log "No flow gap after ${MAX_WAIT_SEC}s; DEFERRING deploy ($what) — an in-flight flow or render holds the slot. Retry lands on the next timer tick; pass --force-flow-reset to override." WARN
      write_status deferred-active-flow "$(git -C "$DEPLOY_DIR" rev-parse HEAD | tr -d '[:space:]')" "" "" "deferred: stack busy after ${MAX_WAIT_SEC}s wait before $what"
      exit 0
    fi
  fi
}

# Step 10 (see header). Never fails the pass: a failure sets CLI_ENV_NOTE, which
# write_status appends to whatever this pass reports. rc 3 = this host never
# installed the host CLI.
sync_host_cli_env() {
  [ -f "$CLI_VENV_SYNC" ] || return 0
  local rc=0
  timeout "$CLI_VENV_TIMEOUT_SEC" bash "$CLI_VENV_SYNC" >>"$LOG_FILE" 2>&1 || rc=$?
  case "$rc" in
    0|3) return 0 ;;
    124) CLI_ENV_NOTE="host CLI env sync timed out after ${CLI_VENV_TIMEOUT_SEC}s" ;;
    *) CLI_ENV_NOTE="host CLI env sync failed (rc=$rc)" ;;
  esac
  log "$CLI_ENV_NOTE — the CLI still runs this clone's code on its previous dependency set, and the launcher retries on the next command. Log: ~/.poindexter/cli-venv-sync.log" ERROR
  return 0
}

reset_at_epoch=""
if [ "$need_reset" = "1" ]; then
  wait_for_gap_or_defer "reset ($behind_raw commit(s) behind)"
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

# Since when the clone has been on this tree. The bounce (step 7) leaves alone a
# container that started after it: that container already loaded the tree.
# After a reset it is the reset. Already current means something else moved
# the clone here — a pass that reset and then failed or died, the brain's
# migration-drift probe (reset --hard, then its own `docker restart
# poindexter-worker`), a hand fast-forward — and git's reflog records when.
# The entry counts only if it names HEAD and is no older than HEAD's own commit:
# the clone cannot reach a commit before it exists, and a selector read as an
# index (HEAD@{0}) would otherwise date the move to 1970 and skip every
# container. Otherwise fall back to now, which is still before this pass has
# touched a container.
tree_since_epoch="$reset_at_epoch"
if [ -z "$tree_since_epoch" ]; then
  reflog_sha=""; reflog_sel=""; moved_at=""
  read -r reflog_sha reflog_sel < <(git -C "$DEPLOY_DIR" reflog -1 --date=unix --format='%H %gd' HEAD 2>/dev/null)
  [ "$reflog_sha" = "$head_sha" ] && [[ "$reflog_sel" =~ @\{([0-9]+)\}$ ]] && moved_at="${BASH_REMATCH[1]}"
  committed_at="$(git -C "$DEPLOY_DIR" log -1 --format=%ct HEAD 2>/dev/null)"
  if [[ "$moved_at" =~ ^[0-9]+$ && "$committed_at" =~ ^[0-9]+$ ]] && [ "$moved_at" -ge "$committed_at" ]; then
    tree_since_epoch="$moved_at"
  else
    tree_since_epoch="$(date -u +%s)"
  fi
fi

if [ "$NO_RESTART" = "1" ]; then
  log "--no-restart set; code synced on disk, containers left as-is."
  sync_host_cli_env
  write_status synced-norestart "$head_sha" "" "" ""
  exit 0
fi

last_deployed="$(cat "$MARKER_FILE" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$last_deployed" ]; then
  printf '%s' "$head_sha" > "$MARKER_FILE"
  log "No prior deploy marker; recorded baseline $short_head without restarting."
  sync_host_cli_env
  write_status baseline-recorded "$head_sha" "" "" ""
  exit 0
fi
if [ "$last_deployed" = "$head_sha" ]; then
  log "Containers already on $short_head; nothing to restart."
  sync_host_cli_env
  write_status synced-no-change "$head_sha" "" "" ""
  exit 0
fi

last_short="${last_deployed:0:9}"
log "Code advanced $last_short -> $short_head; deploying."

# poindexter#1068: the gap wait above guards only the RESET. A pass that finds
# the checkout already current (someone fast-forwarded the deploy clone by
# hand, or the previous pass reset then failed) skipped it, yet still goes on
# to rebuild, compose-apply and bounce the worker below. 2026-09-22 02:20 that
# restarted poindexter-worker 43 s into a media render with
# media_render_running true the whole time. So when this pass did not reset,
# hold here instead: everything from this point on restarts containers.
if [ -z "$reset_at_epoch" ]; then
  wait_for_gap_or_defer "restarting onto $short_head (checkout already current)"
fi

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
  # brain lives under poindexter/ (poindexter#1046 step 2). brain-daemon is the
  # only image that bakes it: the worker bind-mounts src/cofounder_agent, and
  # auto-embed's own entry below already covers poindexter/.
  ['^src/cofounder_agent/poindexter/(brain/|__init__\.py$)']="brain-daemon"
  # Every service built from Dockerfile.worker, not just the first two. They
  # bind-mount src/cofounder_agent over the baked `COPY . .`, so source edits
  # only need the restart below — but the poetry-installed dependency layer and
  # the Dockerfile itself are baked. pipeline-bot was missing: every dependency
  # bump left it on the old packages (verify-deploy-identity flagged it stale
  # 2026-09-28, image a week older than its poetry.lock). The Dockerfile path
  # also read scripts/Dockerfile.worker, which does not exist — the file lives
  # in the build context — so a Dockerfile edit rebuilt none of the three.
  # demo-recorder is the fourth: a profile-gated `compose run --rm` one-shot
  # that builds this Dockerfile into the worker's own image tag. Naming it
  # costs a cached second build of that tag, and it is never started here:
  # it has no running service container, so 6a-bis leaves it parked.
  ['^src/cofounder_agent/(pyproject\.toml|poetry\.lock|Dockerfile\.worker)$']="worker prefect-worker pipeline-bot demo-recorder"
  ['^scripts/Dockerfile\.gpu-exporter$|^scripts/nvidia-smi-exporter\.py$']="gpu-exporter"
  # Both voice agents build this Dockerfile into one image and both are
  # profile-gated. On the operator host both are parked (`voice` since
  # 2026-08-19; `voice-dev` since 2026-06-21, the paid claude -p path), so a
  # rebuild refreshes the image and 6a-bis leaves them stopped. One the
  # operator has started by hand is live, so it is recreated onto the new image.
  ['^scripts/Dockerfile\.voice-agent$']="voice-agent-livekit voice-agent-claude-code"
  # backup-offsite/ is a SIBLING of backup/, so '^scripts/backup/' never
  # matched it — the offsite runner could change without a rebuild.
  ['^scripts/Dockerfile\.backup$|^scripts/backup/|^scripts/backup-offsite/']="backup-daily backup-hourly backup-offsite"
  # GPU sidecars — each bakes ONE server .py, which is the whole reason they
  # need a rebuild at all (the images themselves exist for torch/CUDA deps).
  ['^scripts/(Dockerfile\.image-gen|image-gen-server\.py)$']="image-gen-server"
  ['^scripts/(Dockerfile\.wan|wan-server\.py)$']="wan-server"
  ['^scripts/(Dockerfile\.stable-audio|stable-audio-server\.py)$']="stable-audio-server"
  ['^scripts/(Dockerfile\.rife|rife-server\.py)$']="rife-server"
  ['^scripts/Dockerfile\.chatterbox$|^scripts/tts_sidecars/']="chatterbox"
  ['^scripts/(Dockerfile\.comfyui|comfyui-extra-model-paths\.yaml)$']="comfyui"
  # auto-embed bakes the whole poindexter/ package (one COPY since
  # poindexter#1046) into a minimal image (short pip list, no poetry deps). It
  # cannot bind-mount the tree the worker does: adding a single COPY once let
  # three LLM providers register whose SDKs the image lacks, and every
  # embedding store failed. So it is baked on purpose — and therefore must
  # rebuild when poindexter/ changes, which is most backend merges; compose
  # recreates it each time because its image content genuinely changed.
  ['^scripts/(Dockerfile\.auto-embed|auto-embed\.py)$|^src/cofounder_agent/poindexter/']="auto-embed"
)
rebuild_services=""; diff_ok=0
if diff_paths="$(git -C "$DEPLOY_DIR" diff --name-only "$last_deployed" "$head_sha" 2>/dev/null)"; then
  diff_ok=1
  for re in "${!REBUILD_MAP[@]}"; do
    # here-string, not `echo | grep -q`: under pipefail a diff longer than the pipe
    # buffer makes echo take SIGPIPE when grep -q exits early, and the map entry
    # silently does not fire (stack#3626 found this shape in unit-tests.yml).
    if grep -qE "$re" <<<"$diff_paths"; then
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
  grep -qE '^mcp-server/' <<<"$diff_paths" && mcp_changed=1
  grep -qE '^mcp-server/(pyproject\.toml|uv\.lock)$' <<<"$diff_paths" && mcp_deps_changed=1
else
  mcp_changed=1; mcp_deps_changed=1
fi
rebuild_services="$(echo "$rebuild_services" | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ' | sed 's/ $//')"

build_failed=0
# A service rolled back at THIS sha is not rebuilt again — the same image would
# fail the same gate every 10 minutes. The marker clears itself when HEAD moves.
gate_skipped=""
if [ -n "$rebuild_services" ] && [ -f "$ROLLBACK_MARKER_FILE" ]; then
  rb_sha="$(head -n1 "$ROLLBACK_MARKER_FILE" 2>/dev/null | awk '{print $1}')"
  if [ "$rb_sha" = "$head_sha" ]; then
    rb_services="$(head -n1 "$ROLLBACK_MARKER_FILE" | cut -d' ' -f2-)"
    kept=""
    for svc in $rebuild_services; do
      case " $rb_services " in *" $svc "*) gate_skipped="${gate_skipped:+$gate_skipped }$svc" ;; *) kept="${kept:+$kept }$svc" ;; esac
    done
    rebuild_services="$kept"
    [ -n "$gate_skipped" ] && log "Not rebuilding $gate_skipped: rolled back at $short_head (deploy-rolled-back-sha); merge a fix to retry." WARN
  fi
fi

# ---- which rebuilt services are live? (read BEFORE anything is touched) ----
# Naming a service on `docker compose up` STARTS it, whatever its `profiles:`
# say (verified on compose 5.5.1). So 6a-bis must never name, and the health
# gate must never watch, a rebuilt service that is not live, and neither may
# rest on the recreate plan alone: when the plan cannot run, or compose-apply
# failed and there is no plan, the fallbacks would otherwise reach a parked
# service (voice-agent-livekit, parked since 2026-08-19, is in REBUILD_MAP).
# Building is unaffected. `build <svc>` leaves a stopped service stopped, and
# compose moves it onto the new image the next time it starts it.
#
# service_state <svc> -> running | stopped | unknown. It goes through
# start-stack.sh like every compose call here, so it queries the project the
# apply uses and can never match a same-named service of another project.
# `ps -q <svc>` lists that service's running containers. Naming the service
# enables its profile for the query, so a parked one reads "stopped". It
# leaves out `compose run` one-offs, so a demo-recorder bake in flight does
# not count; `-a` would count that and every exited container. Each id is
# confirmed with docker inspect in case a compose's `ps` lists stopped
# containers. A restarting (crash-looping) container IS running to docker,
# which is right: it is meant to be up, on the new image, in front of the gate.
service_state() { # service_state <compose service>
  local out id
  out="$(bash "$DEPLOY_DIR/scripts/start-stack.sh" ps -q "$1" 2>>"$LOG_FILE")" || { echo unknown; return; }
  while IFS= read -r id; do
    [[ "$id" =~ ^[0-9a-f]{12,64}$ ]] || continue  # stdout is data (see the stranded sweep)
    if [ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" = "true" ]; then
      echo running; return
    fi
  done <<<"$out"
  echo stopped
}
declare -A state_before=()
for svc in $rebuild_services; do state_before[$svc]="$(service_state "$svc")"; done

gate_pre_ok=0
if [ -n "$rebuild_services" ] && [ "$NO_GATE" = "0" ] && [ -f "$HEALTH_GATE" ]; then
  # shellcheck disable=SC2086
  if python3 "$HEALTH_GATE" snapshot --services $rebuild_services > "$GATE_SNAPSHOT_FILE" 2>>"$LOG_FILE"; then gate_pre_ok=1; else log "health gate: snapshot failed; rollback unavailable this pass" WARN; fi
fi
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
# The recreate check (6a-bis) uses this to tell "compose-apply recreated it"
# from "it was already running that image", and "parked before this pass" from
# "started by this pass and then died".
apply_started_epoch="$(date -u +%s)"
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

# ---- recreate check: is every rebuilt service on its new image? (6a-bis) ----
# compose-apply above already recreates a container whose rebuilt image has
# different content — it compares the platform manifest digest it recorded on
# the container with the one the tag now names — and leaves one alone when the
# rebuild changed nothing. This step checks that it did, per service, with the
# same comparison, and force-recreates only what is still on the previous
# image: normally nothing.
#
# History, because the wrong version of this cost a restart per deploy. On
# 2026-09-22 the identity check (step 6d) reported five just-rebuilt services
# as "rebuilt and never recreated", and a `--dry-run` showed compose leaving
# them `Running`. The conclusion drawn — compose never recreates a same-tag
# rebuild — was wrong: under the containerd image store every build mints a
# new image ID (the OCI index carries a fresh attestation manifest) around an
# unchanged platform manifest, so those five were no-op rebuilds that compose
# correctly left alone, and the ID comparison was the false positive. The step
# written from it force-recreated EVERY rebuilt service, so each one compose
# had just recreated started a second time about a minute later: 16 of 48
# brain starts from 09-20 to 09-27 came 72-138 s after the previous one. The
# 09-27 deploy of 49d4052c7 shows it plainly: `Container poindexter-brain-daemon
# Recreate` in step 6, then a second recreate here.
#
# Parked services are left alone, and that is decided from liveness
# (service_state above) BEFORE the plan, so it holds on every path. A rebuilt
# service is live if it was running before this pass touched it, or is running
# after compose-apply. Either check alone loses one: "after" misses a service
# compose-apply recreated that then died, which must still be gated; "before"
# misses a game-mode-parked sidecar the apply started. A service that is
# neither is parked (voice). It is never passed to the plan, never named in
# `up --force-recreate` (which enables its profile and starts it), and never
# gated below, where "exited" reads as a failed deploy whose rollback starts it.
# This runs even when compose-apply failed: the gate needs it then too. A state
# that cannot be read starts nothing and withholds the marker.
#
# Fail-safe in one direction only: a LIVE service the plan cannot account for
# is recreated, exactly as before. A failed recreate withholds the marker so
# the next pass retries — the service would otherwise sit on the old image with
# the pass recorded as deployed.
recreate_now=""; recreate_failed=0; parked_services=""
live_services=""; state_unknown=""; state_failed=0
for svc in $rebuild_services; do
  before="${state_before[$svc]:-unknown}"; now="(not read)"
  [ "$before" = "running" ] || now="$(service_state "$svc")"
  case "$before/$now" in
    running/*|*/running) live_services="${live_services:+$live_services }$svc" ;;
    stopped/stopped)
      parked_services="${parked_services:+$parked_services }$svc"
      log "  not recreating $svc: not running before this pass or after compose-apply (parked); a named recreate would start it" ;;
    *) state_unknown="${state_unknown:+$state_unknown }$svc" ;;
  esac
done
if [ -n "$state_unknown" ]; then
  state_failed=1
  log "could not read whether $state_unknown is running (start-stack.sh ps failed); not recreated or gated, since that could start a parked service — marker withheld, retries next cycle." ERROR
fi
if [ -n "$live_services" ] && [ "$apply_failed" = "0" ]; then
  plan=""
  if [ -f "$HEALTH_GATE" ]; then
    # shellcheck disable=SC2086
    if ! plan="$(python3 "$HEALTH_GATE" recreate-plan --since "$apply_started_epoch" \
                   --services $live_services 2>>"$LOG_FILE")"; then
      log "recreate check: could not compare images; recreating every live rebuilt service to be safe" WARN
      plan=""
    fi
  fi
  for svc in $live_services; do
    action=""; why=""
    while IFS=$'\t' read -r p_action p_svc p_why; do
      if [ "$p_svc" = "$svc" ]; then action="$p_action"; why="$p_why"; break; fi
    done <<<"$plan"
    case "$action" in
      skip)
        log "  not recreating $svc: $why" ;;
      parked)
        parked_services="${parked_services:+$parked_services }$svc"
        log "  not recreating $svc: $why" ;;
      *)
        recreate_now="${recreate_now:+$recreate_now }$svc"
        log "  recreating $svc: ${why:-no image comparison available; recreating to be safe}" WARN ;;
    esac
  done
  if [ -n "$recreate_now" ]; then
    # shellcheck disable=SC2086
    if bash "$DEPLOY_DIR/scripts/start-stack.sh" up -d --no-build --no-deps \
         --force-recreate $recreate_now >>"$LOG_FILE" 2>&1; then
      log "  recreated: $recreate_now"
    else
      recreate_failed=1
      log "force-recreate of $recreate_now failed; still on the previous image — marker withheld, retries next cycle." ERROR
    fi
  fi
fi

# ---- bounce-on-change with redundancy guard --------------------------------
# Once per tree. A pass that did NOT reset (HEAD unchanged since the last pass)
# and whose HEAD is already the tree the containers were bounced onto is a retry
# of some other failed step; bouncing again would kill whatever the worker is
# doing for nothing (stack#3661). No bounce record yet (first pass after this
# change, or the file was removed) means "unknown": bounce once and record,
# exactly the pre-3661 behaviour.
#
# Per container, a process that started after the clone reached this tree
# (tree_since_epoch, above) is already running it and is skipped: one that
# compose-apply or the rebuilt-service recreate started moments ago, or one the
# drift probe restarted after moving the clone. Until 2026-09-28 this check ran
# only when the same pass had reset, so a pass that found the clone already
# current `docker restart`ed a container it had itself just recreated, seconds
# earlier. That hit worker on every such dependency deploy (it is in REBUILD_MAP
# and in RESTART_CONTAINERS), and pipeline-bot too once stack#4144 mapped it.
restart_failed=0; restarted=""; skipped=""; bounce_skipped=0
last_bounced="$(cat "$BOUNCE_MARKER_FILE" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$reset_at_epoch" ] && [ -n "$last_bounced" ] && [ "$last_bounced" = "$head_sha" ]; then
  bounce_skipped=1
  log "Containers already restarted onto $short_head on an earlier pass; not bouncing again (only the step that failed retries)."
fi
for c in "${RESTART_CONTAINERS[@]}"; do
  [ "$bounce_skipped" = "1" ] && break
  if ! docker container inspect "$c" >/dev/null 2>&1; then
    log "  skip '$c' (not present)"; continue
  fi
  # Empty means no guard (bounce): in $((...)) it would read as epoch 0 and
  # skip every container.
  if [ -n "$tree_since_epoch" ]; then
    started_at="$(docker container inspect -f '{{.State.StartedAt}}' "$c" 2>/dev/null)"
    started_epoch="$(date -u -d "$started_at" +%s 2>/dev/null || echo "")"
    if [ -n "$started_epoch" ] && [ "$started_epoch" -gt $((tree_since_epoch + SKEW_MARGIN_SEC)) ]; then
      skipped="$skipped$c "; log "  skip '$c' (already restarted onto this tree at $started_at)"; continue
    fi
  fi
  if docker restart "$c" >>"$LOG_FILE" 2>&1; then
    restarted="${restarted:+$restarted,}$c"; log "  restarted '$c'"
  else
    restart_failed=1; log "  FAILED to restart '$c'" ERROR
  fi
done
# Record the tree the containers are now on -- even if another step of this pass
# fails and withholds MARKER_FILE, the next pass must not bounce them again.
[ "$restart_failed" = "0" ] && printf '%s' "$head_sha" > "$BOUNCE_MARKER_FILE"

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
  # A container name and nothing else. start-stack.sh's stdout is DATA here,
  # but any preamble output that lands on stdout becomes a fake "container":
  # on 2026-09-23 a Grafana host notice (#3976) made every pass run
  # `docker start "Grafana dashboard links will point at: …"`, fail, and exit
  # 1 — blocking all deploys. start-stack.sh now sends that to stderr; this
  # guard is so the NEXT stray echo warns instead of halting the fleet.
  if ! [[ "$c" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    log "  ignoring non-container line on start-stack.sh stdout: '$c' (it should go to stderr)" WARN
    continue
  fi
  log "  stranded '$c' (created, never started) — starting" WARN
  if docker start "$c" >>"$LOG_FILE" 2>&1; then
    stranded_started="${stranded_started:+$stranded_started,}$c"
  else
    stranded_failed=1; log "  FAILED to start stranded '$c'" ERROR
  fi
done < <(bash "$DEPLOY_DIR/scripts/start-stack.sh" ps --status=created --format '{{.Name}}' 2>/dev/null || true)
[ -n "$stranded_started" ] && log "Recovered stranded containers: $stranded_started" WARN

# ---- health gate: did what we just rebuilt / restarted actually come up? ----
# Rebuilt services: watch until healthy; on a definitive failure roll back onto
# the pre-rebuild image (from the snapshot above) and page critical with the
# container's last log lines. Bounced bind-mount containers: watch and page only.
gate_result=""; gate_rolled_back=""
if [ "$NO_GATE" = "0" ] && [ -f "$HEALTH_GATE" ] && { [ -n "$rebuild_services" ] || [ -n "$restarted" ]; }; then
  gate_units=""
  if [ "$build_failed" = "0" ]; then
    # Live services only (6a-bis), minus any the plan found parked. A parked
    # service is not meant to be running: gating it would read "exited" as a
    # broken image, and the rollback (`up --force-recreate`) would start it.
    # Liveness is read whether or not compose-apply succeeded, so this holds
    # on a pass with no plan too. An unreadable one is not gated either.
    for svc in $live_services; do
      case " $parked_services " in *" $svc "*) continue ;; esac
      gate_units="${gate_units:+$gate_units }$svc"
    done
  fi
  for c in $(echo "$restarted" | tr ',' ' '); do gate_units="${gate_units:+$gate_units }container:$c"; done
  if [ -n "$gate_units" ]; then
    gate_args=(verify --sha "$head_sha" --stack-cmd "bash $DEPLOY_DIR/scripts/start-stack.sh")
    if [ "$gate_pre_ok" = "1" ]; then gate_args+=(--snapshot "$GATE_SNAPSHOT_FILE"); else gate_args+=(--no-rollback); fi
    # shellcheck disable=SC2086
    gate_json="$(python3 "$HEALTH_GATE" "${gate_args[@]}" --services $gate_units 2>>"$LOG_FILE")"; gate_rc=$?
    case "$gate_rc" in
      0) log "health gate: all healthy ($gate_units)"; gate_result="healthy" ;;
      2) gate_rolled_back="$(printf '%s' "$gate_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(" ".join(k for k,v in d.items() if v.get("rolled_back")))' 2>/dev/null)"
         printf '%s %s\n' "$head_sha" "$gate_rolled_back" > "$ROLLBACK_MARKER_FILE"
         log "health gate: ROLLED BACK $gate_rolled_back at $short_head — previous image restored, critical alert sent, this sha will not be rebuilt for them. $gate_json" ERROR
         gate_result="rolled back: $gate_rolled_back" ;;
      1) log "health gate: unhealthy without rollback: $gate_json" ERROR; gate_result="unhealthy (see alert)" ;;
      *) log "health gate: gate itself failed (rc=$gate_rc): $gate_json" ERROR; gate_result="gate error rc=$gate_rc" ;;
    esac
  fi
fi

# ---- identity check (step 6d, 2026-09-20) ---------------------------------
# The health gate above answers "is it healthy". This answers "is it running
# the code we just deployed" — a different question with a different failure.
# A container running month-old code is perfectly healthy: it passes the gate,
# passes the restart-loop probe, and `docker ps` shows it green. That is how a
# brain change was merged, pulled, and never reached the running daemon on
# 2026-09-20 (the brain image is BAKED; a pull moves no code into it).
# Advisory: it reports, it does not roll back — a stale image is a missed
# rebuild, not a broken deploy, and auto-rebuilding here would race step 6b.
IDENTITY_CHECK="$DEPLOY_DIR/scripts/linux/verify_deploy_identity.py"
if [ "$NO_GATE" = "0" ] && [ -f "$IDENTITY_CHECK" ]; then
  identity_out="$(python3 "$IDENTITY_CHECK" --repo "$DEPLOY_DIR" 2>>"$LOG_FILE")"; identity_rc=$?
  case "$identity_rc" in
    0) log "identity check: every running container matches the checkout" ;;
    1) log "identity check: STALE container(s) — running code that is not the checkout. $(printf '%s' "$identity_out" | tr '\n' ' ')" WARN ;;
    *) log "identity check: could not run (rc=$identity_rc)" WARN ;;
  esac
fi

# ---- game-mode re-park (step 6c) ------------------------------------------
# Same keys as services/game_mode.py + brain/compose_drift_probe.py; the
# timestamp compare happens in Postgres (ISO-8601 in bash is not worth getting
# wrong), NULLIF turns the "off" sentinel '' into no row. Fail-open: an
# unreachable DB means "not in game mode" — never block a deploy on this.
game_mode_active() {
  local active
  active="$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tA \
      -c "SELECT (NULLIF(value,'')::timestamptz > now())::int FROM app_settings WHERE key='game_mode_until'" 2>/dev/null \
      | tr -d '[:space:]')" || return 1
  [ "${active:-0}" = "1" ]
}
game_mode_setting() { # game_mode_setting <key> <default>
  local v
  v="$(docker exec poindexter-postgres-local psql -U poindexter -d poindexter_brain -tA \
      -c "SELECT value FROM app_settings WHERE key='$1'" 2>/dev/null | tr -d '[:space:]')"
  echo "${v:-$2}"
}
reparked=""; repark_failed=0
if game_mode_active; then
  prefix="$(game_mode_setting game_mode_container_prefix poindexter-)"
  for svc in $(game_mode_setting game_mode_parked_services "speaches,chatterbox,stable-audio-server,image-gen-server,wan-server,comfyui" | tr ',' ' '); do
    # Resolve by compose service label: container_name isn't always prefix+service
    # (stable-audio-server runs as poindexter-stable-audio).
    c="$(docker ps -a --filter "label=com.docker.compose.service=${svc}" --format '{{.Names}}' 2>/dev/null | head -n1)"
    c="${c:-${prefix}${svc}}"
    [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "true" ] || continue
    if docker stop -t 20 "$c" >>"$LOG_FILE" 2>&1; then
      reparked="${reparked:+$reparked,}$c"
    else
      repark_failed=1; log "game mode: FAILED to re-park '$c'" ERROR
    fi
  done
  [ -n "$reparked" ] && log "game mode active: re-parked $reparked (compose-apply had started them)" WARN
fi

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

# The mcp_changed / mcp_deps_changed flags come from the MARKER-based diff, which
# is identical on every retrying pass; without this record the connector was
# re-synced and restarted every 10 minutes alongside the worker (stack#3661).
last_connector="$(cat "$CONNECTOR_MARKER_FILE" 2>/dev/null | tr -d '[:space:]')"
connector_done_for_head=0; [ -n "$last_connector" ] && [ "$last_connector" = "$head_sha" ] && connector_done_for_head=1
if mcp_unit_loaded; then
  need_uv_sync=0; need_mcp_restart=0
  if [ "$connector_done_for_head" = "1" ]; then
    log "connector: already synced+restarted for $short_head on an earlier pass; skipping."
  else
    [ "$mcp_deps_changed" = "1" ] && need_uv_sync=1
    [ "$mcp_changed" = "1" ] && need_mcp_restart=1
  fi
  [ -x "$mcp_venv_python" ] || need_uv_sync=1   # self-heal a missing venv (always)
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
  [ "$mcp_failed" = "0" ] && printf '%s' "$head_sha" > "$CONNECTOR_MARKER_FILE"
elif [ "$mcp_changed" = "1" ]; then
  log "connector: mcp-server/ changed but $MCP_UNIT is not installed on this host; skipping."
fi

# ---- outcome (step independence: marker only on a fully-clean pass) --------
if [ "$build_failed" = "0" ] && [ "$apply_failed" = "0" ] && [ "$recreate_failed" = "0" ] && [ "$state_failed" = "0" ] && [ "$restart_failed" = "0" ] && [ "$stranded_failed" = "0" ] && [ "$mcp_failed" = "0" ]; then
  printf '%s' "$head_sha" > "$MARKER_FILE"
  detail=""
  [ -n "$rebuild_services" ] && detail="rebuilt: $rebuild_services"
  # Normally empty: compose-apply recreates what changed. Non-empty means it
  # left something on the previous image and this pass had to repair it.
  [ -n "$recreate_now" ] && detail="${detail:+$detail; }recreated after compose-apply: $recreate_now"
  [ -n "$parked_services" ] && detail="${detail:+$detail; }left parked: $parked_services"
  [ -n "$skipped" ] && detail="${detail:+$detail; }skipped already-fresh: $skipped"
  [ "$bounce_skipped" = "1" ] && detail="${detail:+$detail; }restarts skipped: containers already on $short_head"
  # Surface the recovery in the status file even on a clean pass — a sweep that
  # had to act means the apply raced, and a silent self-heal is how a recurring
  # fault stays invisible.
  [ -n "$stranded_started" ] && detail="${detail:+$detail; }recovered stranded: $stranded_started"
    [ -n "$gate_result" ] && detail="${detail:+$detail; }health gate: $gate_result"
    [ -n "$gate_skipped" ] && detail="${detail:+$detail; }not rebuilt (rolled back at this sha): $gate_skipped"
  log "Pipeline now running $short_head. ${detail}"
  write_status deployed "$head_sha" "$last_deployed" "$restarted" "$detail"
else
  steps=""
  [ "$build_failed" = "1" ] && steps="${steps}image-rebuild "
  [ "$apply_failed" = "1" ] && steps="${steps}compose-apply "
  [ "$recreate_failed" = "1" ] && steps="${steps}recreate-rebuilt "
  [ "$state_failed" = "1" ] && steps="${steps}service-state "
  [ "$restart_failed" = "1" ] && steps="${steps}container-restart "
  [ "$stranded_failed" = "1" ] && steps="${steps}stranded-start "
  [ "$mcp_failed" = "1" ] && steps="${steps}mcp-connector "
  note=""; [ "$bounce_skipped" = "1" ] && note="; restarts skipped: containers already on $short_head"
  log "Deploy pass incomplete (failed: $steps); NOT recording marker — retries next cycle$note." ERROR
  write_status error "$head_sha" "$last_deployed" "$restarted" "failed steps: $steps$note"
  exit 1
fi

# ---- host CLI environment (step 10) ---------------------------------------
# Last, once the pass is recorded: this step can neither delay a container step
# nor cost the marker if the unit's TimeoutStartSec cuts it short. A pass with a
# failed step (exit 1 above) leaves it to the next pass and to the launcher.
sync_host_cli_env
if [ -n "$CLI_ENV_NOTE" ]; then
  STATUS_AMEND=1
  write_status deployed "$head_sha" "$last_deployed" "$restarted" "$detail"
fi
