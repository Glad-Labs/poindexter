#!/usr/bin/env bash
# install-deploy-sync.sh — make the deploy driver run merged code, behind the
# launcher's last-known-good fallback, move the docker watchdog and the GPU
# scraper onto the deploy clone, and refresh the connector's and the recovery
# agent's units when this host already has them (Glad-Labs/glad-labs-stack#4172,
# #4188, #4232).
#
# Host setup; safe to re-run, and re-run it after any change to the launcher or
# to one of the unit templates, because the installed copies never update on
# their own. It:
#   1. copies the deploy clone's scripts/linux/deploy-sync-launcher.sh to
#      ~/.poindexter/deploy-sync/deploy-sync-launcher.sh. It installs a COPY
#      on purpose. The unit's entry point must not live in the clone that every
#      pass resets, or a broken merge of the launcher itself would brick the
#      syncer.
#   2. seeds the last-known-good driver, if there is none yet, from the driver
#      this host's unit runs today, the one proven here. It never seeds from the
#      deploy clone: that is the copy the launcher judges, and a fallback
#      identical to it could not rescue anything. With no unit installed
#      before, the merged driver's first clean pass creates the copy.
#   3. renders poindexter-deploy-sync.service (User=, and ExecStart= the
#      installed launcher) and poindexter-docker-watchdog.service (User=, and
#      ExecStart= the deploy clone's docker-watchdog.sh) into
#      /etc/systemd/system with both timers, then daemon-reloads, enables both
#      timers and restarts them.
#   4. renders poindexter-gpu-scraper.service (User=, WorkingDirectory= and
#      ExecStart= on the deploy clone) into the same place. It is a
#      long-running daemon, so it needs no launcher: the deploy pass restarts
#      it whenever a merge changes a file it loads at start (step 8b), and this
#      render is what makes those restarts load merged code. On a host that
#      already had the unit, a running scraper is restarted onto the clone now
#      (try-restart: a stopped one stays stopped, and enablement is left as it
#      was). On a host that did not, the unit is installed but not enabled:
#      gpu_metrics is optional, and the scraper needs host python3-asyncpg and
#      python3-httpx, so enabling it is the operator's call.
#   5. refreshes poindexter-mcp-http.service and poindexter-recovery-agent.service
#      (User=, WorkingDirectory= and ExecStart= on the deploy clone) if this host
#      ALREADY has them in the unit dir. It never installs or enables either:
#      the connector needs a uv venv in the clone, the recovery agent a
#      bootstrap token and a sudoers grant, and whether a host runs them is its
#      operator's call. Install one by hand first (its template's header says
#      how); this then keeps it on the template. Every other line comes from the
#      template, so a host-specific value (a different port or API URL) belongs
#      in a drop-in, `sudo systemctl edit <unit>`, which this never touches. When
#      the installed unit differs from the template beyond those three
#      directives, the lines it replaces are printed. A unit whose non-comment
#      lines changed is try-restarted after the daemon-reload (a stopped one
#      stays stopped; a restart drops the connector's open sessions and kills a
#      recovery action the agent has in flight, so pick a quiet moment). One that
#      differs only in comments is rewritten without a restart, and an identical
#      one is left alone. The connector is left as it was, with the command that
#      builds its venv, while the clone has no mcp-server/.venv yet.
#   6. runs one deploy pass through the launcher now and prints its report.
#      Pass --no-start to skip that; the timer's next fire runs it instead.
#
# Usage: bash ~/.poindexter/deploy/glad-labs-stack/scripts/linux/install-deploy-sync.sh [--no-start]
# Run it as your login. It calls sudo itself for /etc and systemctl; invoked
# through sudo, it still installs for SUDO_USER and leaves the files under
# ~/.poindexter owned by that user.
# Env: POINDEXTER_DEPLOY_ROOT, POINDEXTER_DEPLOY_SYNC_HOME (both shared with the
#      launcher), POINDEXTER_UNIT_DIR (default /etc/systemd/system).
set -euo pipefail

START=1
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    -h|--help) awk 'NR > 1 && /^#/ { print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "[install-deploy-sync] unknown argument: $arg" >&2; exit 2 ;;
  esac
done

RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_HOME="$HOME"
if [ "$(id -u)" = "0" ] && [ -n "${SUDO_USER:-}" ]; then
  # sudo may have pointed HOME at /root; the files belong under the operator's.
  RUN_HOME="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)"
  RUN_HOME="${RUN_HOME:-$HOME}"
fi
DEPLOY_ROOT="${POINDEXTER_DEPLOY_ROOT:-$RUN_HOME/.poindexter/deploy/glad-labs-stack}"
STATE_DIR="${POINDEXTER_DEPLOY_SYNC_HOME:-$RUN_HOME/.poindexter/deploy-sync}"
UNIT_DIR="${POINDEXTER_UNIT_DIR:-/etc/systemd/system}"
LAUNCHER_SRC="$DEPLOY_ROOT/scripts/linux/deploy-sync-launcher.sh"
LAUNCHER="$STATE_DIR/deploy-sync-launcher.sh"
LKG="$STATE_DIR/last-known-good.sh"
LKG_META="$STATE_DIR/last-known-good.meta"
SYNC_UNIT="poindexter-deploy-sync"
WATCHDOG_UNIT="poindexter-docker-watchdog"
SCRAPER_UNIT="poindexter-gpu-scraper"
MCP_UNIT="poindexter-mcp-http"
AGENT_UNIT="poindexter-recovery-agent"
MCP_PYTHON="$DEPLOY_ROOT/mcp-server/.venv/bin/python"

log() { printf '[install-deploy-sync] %s\n' "$*"; }
die() { printf '[install-deploy-sync] ERROR: %s\n' "$*" >&2; exit 1; }

git -C "$DEPLOY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "no deploy clone at $DEPLOY_ROOT. Create it with scripts/setup-deploy-checkout.sh."
for f in "$LAUNCHER_SRC" "$DEPLOY_ROOT/scripts/linux/deploy-checkout-sync.sh" \
         "$DEPLOY_ROOT/scripts/linux/docker-watchdog.sh" \
         "$DEPLOY_ROOT/infrastructure/systemd/$SYNC_UNIT.service" \
         "$DEPLOY_ROOT/infrastructure/systemd/$SYNC_UNIT.timer" \
         "$DEPLOY_ROOT/infrastructure/systemd/$WATCHDOG_UNIT.service" \
         "$DEPLOY_ROOT/infrastructure/systemd/$WATCHDOG_UNIT.timer" \
         "$DEPLOY_ROOT/scripts/gpu-scraper.py" \
         "$DEPLOY_ROOT/infrastructure/systemd/$SCRAPER_UNIT.service"; do
  [ -f "$f" ] || die "the deploy clone at $DEPLOY_ROOT has no $f. It predates the launcher: bring it to origin/main first (systemctl start $SYNC_UNIT.service), then re-run."
done
bash -n "$LAUNCHER_SRC" || die "$LAUNCHER_SRC fails bash -n; not installing it"
# The connector and the recovery agent are refreshed only where the host already
# has them, so the clone only has to carry their files there: a clone without
# them must not block a host that never ran them. Checked here, like the files
# above, so a refusal happens before anything is written.
refresh_needs() { # refresh_needs <unit> <file>...
  local unit="$1" f; shift
  [ -f "$UNIT_DIR/$unit.service" ] || return 0
  for f in "$@"; do
    [ -f "$f" ] || die "$unit.service is installed here, so this run refreshes it, and the deploy clone at $DEPLOY_ROOT has no $f. Bring the clone to origin/main first (systemctl start $SYNC_UNIT.service), then re-run."
  done
}
refresh_needs "$MCP_UNIT" "$DEPLOY_ROOT/infrastructure/systemd/$MCP_UNIT.service"
refresh_needs "$AGENT_UNIT" "$DEPLOY_ROOT/infrastructure/systemd/$AGENT_UNIT.service" \
  "$DEPLOY_ROOT/scripts/recovery-agent.py"

# ---- 1. the launcher ---------------------------------------------------------
mkdir -p "$STATE_DIR"
cp "$LAUNCHER_SRC" "$LAUNCHER.tmp"
chmod 0755 "$LAUNCHER.tmp"
mv -f "$LAUNCHER.tmp" "$LAUNCHER"
log "installed the launcher: $LAUNCHER (a copy of $LAUNCHER_SRC)"

# ---- 2. the last-known-good driver --------------------------------------------
# The driver the installed unit runs today is the one proven on this host.
prev_exec="$(sed -n 's/^ExecStart=//p' "$UNIT_DIR/$SYNC_UNIT.service" 2>/dev/null | head -n 1 || true)"
prev_driver="${prev_exec%% *}"
if [ -s "$LKG" ]; then
  log "keeping the existing last-known-good driver ($LKG)"
elif [ "$(basename "${prev_driver:-none}")" = "deploy-checkout-sync.sh" ] && [ -f "$prev_driver" ]; then
  if bash -n "$prev_driver"; then
    prev_commit="$(git -C "$(dirname "$prev_driver")" rev-parse --verify -q 'HEAD^{commit}' 2>/dev/null || true)"
    cp "$prev_driver" "$LKG.tmp"
    chmod 0755 "$LKG.tmp"
    mv -f "$LKG.tmp" "$LKG"
    printf 'commit=%s\nblob=%s\nhow=%s\nat=%s\n' "${prev_commit:-unknown}" \
      "$(git hash-object "$LKG" 2>/dev/null || echo unknown)" \
      "seeded from $prev_driver (the unit's previous ExecStart)" \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LKG_META"
    log "seeded the last-known-good driver from $prev_driver, the driver this host ran until now"
  else
    log "WARNING: not seeding the last-known-good driver: $prev_driver fails bash -n. The merged driver's first clean pass creates it."
  fi
else
  log "no deploy driver was installed before, so there is nothing proven here to seed from. The merged driver's first clean pass creates the last-known-good copy."
fi

# Invoked through sudo: everything under ~/.poindexter stays the operator's.
if [ "$(id -u)" = "0" ] && [ "$RUN_USER" != "root" ]; then
  chown -R "$RUN_USER:" "$STATE_DIR"
fi

# ---- 3 - 5. the units ----------------------------------------------------------
# render <unit file> <ExecStart> [WorkingDirectory]; the repo templates ship
# generic placeholders, and only these directives are host-specific.
render() {
  local wd=()
  [ -n "${3:-}" ] && wd=(-e "s|^WorkingDirectory=.*|WorkingDirectory=$3|")
  sed -e "s|^User=.*|User=${RUN_USER}|" -e "s|^ExecStart=.*|ExecStart=$2|" ${wd[@]+"${wd[@]}"} \
    "$DEPLOY_ROOT/infrastructure/systemd/$1"
}

# What systemd acts on. Comments and blank lines change nothing about what runs,
# and they are most of what a template edit touches (#4188 and #4218 were
# comment-only), so they must not decide a restart.
effective() { grep -Ev '^[[:space:]]*([#;]|$)' <<<"$1" || true; }

# Units whose non-comment lines changed, restarted once the daemon has reloaded.
RESTART_UNITS=()

# refresh_unit <unit> <ExecStart> <WorkingDirectory> [<needs> <how to get it>]
# Re-renders a unit this host ALREADY has, onto the deploy clone. It never
# installs one: the connector needs a uv venv and the recovery agent a bootstrap
# token and a sudoers grant, so whether a host runs either is its operator's
# call. <needs> is a file the new ExecStart runs; while it is missing the unit
# stays as it was, because try-restarting onto an interpreter that is not there
# would take a working service down. Host-specific values are not preserved from
# the installed file on purpose: a merge cannot tell a deliberate local value
# from a stale template default, so a changed default would never arrive. They
# belong in a drop-in, which this never touches, and what it replaces is printed.
refresh_unit() {
  local unit="$1" file="$UNIT_DIR/$1.service" old new eff_old eff_new
  if [ ! -f "$file" ]; then
    log "$unit.service is not installed here, so it is left alone (this installer refreshes it, never installs it; its template's header says how)"
    return 0
  fi
  if [ -n "${4:-}" ] && [ ! -x "$4" ]; then
    log "WARNING: $unit.service is installed here but was NOT refreshed: $4 is missing or not executable. ${5:-}"
    return 0
  fi
  old="$(cat "$file")"
  new="$(render "$unit.service" "$2" "$3")"
  if [ "$old" = "$new" ]; then
    log "$unit.service is already current"
    return 0
  fi
  eff_old="$(effective "$old")"
  eff_new="$(effective "$new")"
  printf '%s\n' "$new" | sudo tee "$file" >/dev/null
  if [ "$eff_old" = "$eff_new" ]; then
    log "refreshed $unit.service (only comments differed, so it is not restarted): WorkingDirectory=$3, ExecStart=$2, User=$RUN_USER"
    return 0
  fi
  log "refreshed $unit.service (WorkingDirectory=$3, ExecStart=$2, User=$RUN_USER). These lines of the installed unit were replaced by the template's; a host-specific value belongs in a drop-in (sudo systemctl edit $unit.service):"
  diff --unchanged-line-format= --old-line-format='    - %L' --new-line-format='    + %L' \
    <(printf '%s\n' "$eff_old") <(printf '%s\n' "$eff_new") || true
  RESTART_UNITS+=("$unit.service")
}

render "$SYNC_UNIT.service" "$LAUNCHER" | sudo tee "$UNIT_DIR/$SYNC_UNIT.service" >/dev/null
render "$WATCHDOG_UNIT.service" "$DEPLOY_ROOT/scripts/linux/docker-watchdog.sh" \
  | sudo tee "$UNIT_DIR/$WATCHDOG_UNIT.service" >/dev/null
for timer in "$SYNC_UNIT.timer" "$WATCHDOG_UNIT.timer"; do
  sudo tee "$UNIT_DIR/$timer" < "$DEPLOY_ROOT/infrastructure/systemd/$timer" >/dev/null
done
log "installed $SYNC_UNIT.service (ExecStart=$LAUNCHER) and $WATCHDOG_UNIT.service (ExecStart=$DEPLOY_ROOT/scripts/linux/docker-watchdog.sh), User=$RUN_USER"
# Whether this host ran the scraper before decides what happens to it below.
had_scraper=0
[ -f "$UNIT_DIR/$SCRAPER_UNIT.service" ] && had_scraper=1
render "$SCRAPER_UNIT.service" "/usr/bin/python3 $DEPLOY_ROOT/scripts/gpu-scraper.py" "$DEPLOY_ROOT" \
  | sudo tee "$UNIT_DIR/$SCRAPER_UNIT.service" >/dev/null
log "installed $SCRAPER_UNIT.service (WorkingDirectory=$DEPLOY_ROOT, ExecStart=/usr/bin/python3 $DEPLOY_ROOT/scripts/gpu-scraper.py), User=$RUN_USER"
# The connector's and the agent's units, where the host has them.
refresh_unit "$MCP_UNIT" "$MCP_PYTHON http_server.py" "$DEPLOY_ROOT/mcp-server" "$MCP_PYTHON" \
  "Build the venv with: uv sync --directory $DEPLOY_ROOT/mcp-server (the deploy pass does it too when uv is on its PATH), then re-run this installer."
refresh_unit "$AGENT_UNIT" "/usr/bin/python3 $DEPLOY_ROOT/scripts/recovery-agent.py" "$DEPLOY_ROOT"

sudo systemctl daemon-reload
sudo systemctl enable "$SYNC_UNIT.timer" "$WATCHDOG_UNIT.timer"
# A timer re-reads its schedule only when it restarts. Neither timer replays a
# missed run here: deploy-sync's Persistent stamp is recent, and the watchdog's
# schedule is relative to its own last run.
sudo systemctl restart "$SYNC_UNIT.timer" "$WATCHDOG_UNIT.timer"
if [ "$had_scraper" = "1" ]; then
  # A running scraper still runs the tree its old unit named until it restarts.
  # try-restart leaves a stopped one stopped; enablement is untouched.
  sudo systemctl try-restart "$SCRAPER_UNIT.service"
  log "restarted $SCRAPER_UNIT.service onto the deploy clone if it was running"
else
  log "$SCRAPER_UNIT.service was not installed here before, so it is installed but NOT enabled. To write gpu_metrics from this host: sudo apt install python3-asyncpg python3-httpx && sudo systemctl enable --now $SCRAPER_UNIT.service"
fi
# A running unit keeps the exec its old unit file named until it restarts.
# try-restart leaves a stopped one stopped; enablement is untouched.
for unit in ${RESTART_UNITS[@]+"${RESTART_UNITS[@]}"}; do
  sudo systemctl try-restart "$unit"
  log "restarted $unit onto its refreshed unit if it was running"
done

# ---- 6. prove it -----------------------------------------------------------------
if [ "$START" = "1" ]; then
  log "running one deploy pass through the launcher now (Ctrl-C only stops the waiting; the pass continues under systemd)…"
  sudo systemctl start "$SYNC_UNIT.service" \
    || log "WARNING: that pass failed. See journalctl -u $SYNC_UNIT and ~/.poindexter/deploy-checkout-sync.log"
fi
log "done. Check it any time with: bash $LAUNCHER --status"
POINDEXTER_DEPLOY_ROOT="$DEPLOY_ROOT" POINDEXTER_DEPLOY_SYNC_HOME="$STATE_DIR" HOME="$RUN_HOME" \
  bash "$LAUNCHER" --report
