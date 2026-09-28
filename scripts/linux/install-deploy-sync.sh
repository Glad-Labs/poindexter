#!/usr/bin/env bash
# install-deploy-sync.sh — make the deploy driver run merged code, behind the
# launcher's last-known-good fallback, and move the docker watchdog and the GPU
# scraper onto the deploy clone (Glad-Labs/glad-labs-stack#4172, #4188).
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
#   5. runs one deploy pass through the launcher now and prints its report.
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

# ---- 3 + 4. the units ----------------------------------------------------------
# render <unit file> <ExecStart> [WorkingDirectory]; the repo templates ship
# generic placeholders, and only these directives are host-specific.
render() {
  local wd=()
  [ -n "${3:-}" ] && wd=(-e "s|^WorkingDirectory=.*|WorkingDirectory=$3|")
  sed -e "s|^User=.*|User=${RUN_USER}|" -e "s|^ExecStart=.*|ExecStart=$2|" ${wd[@]+"${wd[@]}"} \
    "$DEPLOY_ROOT/infrastructure/systemd/$1"
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

# ---- 5. prove it -----------------------------------------------------------------
if [ "$START" = "1" ]; then
  log "running one deploy pass through the launcher now (Ctrl-C only stops the waiting; the pass continues under systemd)…"
  sudo systemctl start "$SYNC_UNIT.service" \
    || log "WARNING: that pass failed. See journalctl -u $SYNC_UNIT and ~/.poindexter/deploy-checkout-sync.log"
fi
log "done. Check it any time with: bash $LAUNCHER --status"
POINDEXTER_DEPLOY_ROOT="$DEPLOY_ROOT" POINDEXTER_DEPLOY_SYNC_HOME="$STATE_DIR" HOME="$RUN_HOME" \
  bash "$LAUNCHER" --report
