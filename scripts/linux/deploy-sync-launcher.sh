#!/usr/bin/env bash
# deploy-sync-launcher.sh — run the MERGED deploy driver every fire, and keep a
# last-known-good copy so a broken merge can't brick the syncer that would
# deliver its fix. (Glad-Labs/poindexter#4172)
#
# poindexter-deploy-sync.service execs an INSTALLED COPY of this file:
#
#   ~/.poindexter/deploy-sync/deploy-sync-launcher.sh  this file (the unit's ExecStart)
#   ~/.poindexter/deploy-sync/last-known-good.sh       the last driver that completed a clean pass
#   ~/.poindexter/deploy-sync/last-known-good.meta     where that copy came from
#   ~/.poindexter/deploy-sync/candidate.sh             this fire's staged copy of the merged driver
#
# scripts/linux/install-deploy-sync.sh puts it there. It is a copy, never a
# symlink into a checkout, because it is the one piece a merge must not be able
# to break: it is what recovers from a broken merge. It changes rarely, and when
# the deploy clone's copy moves on it says so (log line, status detail,
# --report). Re-run the installer to pick that up, as for the unit files.
#
# Until 2026-09-28 the unit ran deploy-checkout-sync.sh straight out of the
# operator's working checkout. Only run-session.sh's ff-only pre-flight ever
# advanced that tree, and it rightly skips a dirty one. It skipped 34 runs in a
# row, so the checkout sat 148 commits behind and four merged driver fixes
# (#3984, #4001, #4085, #4144) never ran. Two of them were the guards that stop
# a deploy from bouncing the worker through a media render.
#
# One fire:
#   1. Stage the merged driver: the deploy clone's COMMITTED copy
#      (HEAD:scripts/linux/deploy-checkout-sync.sh) into candidate.sh. It is
#      staged so the pass's own `git reset --hard` never changes the bytes that
#      are running, and so a promotion keeps exactly the bytes that proved
#      themselves.
#   2. Choose:
#        merged copy missing, or fails `bash -n`  -> run the last-known-good copy
#        no last-known-good copy yet              -> run the merged copy (nothing to fall back to)
#        merged copy identical to it              -> run it once (the steady state)
#        otherwise                                -> run the merged copy and judge it (3)
#   3. Judge the run by what it did to the clone, not by what it reported. It
#      "reached origin" if it fetched during its run (FETCH_HEAD rewritten and
#      naming a commit; a failed fetch empties it) and the clone's HEAD is what
#      it fetched.
#        exit 0, reached         -> PROMOTE: its staged bytes become last-known-good.
#        exit 0, fetched, behind -> a deferral (busy stack). Trusted, never
#                                   overridden: the merged driver's busy guard is
#                                   the newer one, and falling back would let an
#                                   older guard bounce the worker through a
#                                   render the newer one saw. Judged again next
#                                   fire.
#        exit != 0, reached      -> a deploy step failed AFTER the clone moved, so
#                                   a fix can still arrive. No fallback.
#        anything else           -> it could not sync (crash, early exit, timeout).
#                                   If origin answers (git ls-remote), run the
#                                   last-known-good copy now: it fetches and
#                                   resets onto whatever fixed the merged one,
#                                   whose first clean pass then promotes it. If
#                                   origin does not answer, neither copy could
#                                   reach it, so wait for the next fire.
#   4. Exit with the status of the last driver that ran.
#
# Every driver run gets SYNC_DRIVER_TIMEOUT_SEC (900 s, the pass budget the unit
# used to enforce on its own). A merged copy that hangs before syncing is killed
# and judged like any other that failed. DEPLOY_SYNC_DRIVER (merged |
# last-known-good), DEPLOY_SYNC_DRIVER_COMMIT and DEPLOY_SYNC_DRIVER_NOTE tell
# the driver which copy it is and why. It writes them into its status file and
# its audit_log heartbeat, and the brain's deploy_sync probe raises
# deploy_sync_driver_fallback whenever a pass ran the last-known-good copy. A
# silent fallback would be the failure this exists to end: merged driver code
# not running, with nothing saying so.
#
# Not covered, so nobody over-trusts it: a merged driver that exits 0 without
# ever moving the clone (a busy guard that is always busy, a skipped reset)
# looks exactly like a deferral from here. The brain's branch-drift canary pages
# when the clone falls behind origin/main.
#
# Flags: --report (this launcher's state; read-only) | --status (the driver's
#        --status, which includes --report) | anything else passes through to
#        the driver.
# Env:   POINDEXTER_DEPLOY_ROOT, POINDEXTER_DEPLOY_SYNC_HOME, SOURCE_REMOTE,
#        SYNC_BRANCH (all shared with the driver), SYNC_DRIVER_TIMEOUT_SEC.
set -uo pipefail

DEPLOY_DIR="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
STATE_DIR="${POINDEXTER_DEPLOY_SYNC_HOME:-$HOME/.poindexter/deploy-sync}"
SOURCE_REMOTE="${SOURCE_REMOTE:-origin}"
SYNC_BRANCH="${SYNC_BRANCH:-main}"
DRIVER_TIMEOUT_SEC="${SYNC_DRIVER_TIMEOUT_SEC:-900}"
DRIVER_REL="scripts/linux/deploy-checkout-sync.sh"
LAUNCHER_REL="scripts/linux/deploy-sync-launcher.sh"
LKG="$STATE_DIR/last-known-good.sh"
LKG_META="$STATE_DIR/last-known-good.meta"
STAGED="$STATE_DIR/candidate.sh"
LOCK_FILE="$STATE_DIR/launcher.lock"
# The driver's log, so one file tells the whole story of a pass.
LOG_FILE="$HOME/.poindexter/deploy-checkout-sync.log"
SELF="${BASH_SOURCE[0]}"

log() { # log <msg> [LEVEL]
  echo "[deploy-sync-launcher] $1"
  printf '%s [%s] [launcher] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${2:-INFO}" "$1" \
    >> "$LOG_FILE" 2>/dev/null || true
}

clone_git() { git -C "$DEPLOY_DIR" "$@" 2>/dev/null; }

lkg_field() { # lkg_field <key>, from last-known-good.meta ("unknown" when absent)
  local v
  v="$(sed -n "s/^$1=//p" "$LKG_META" 2>/dev/null | head -n 1)"
  printf '%s' "${v:-unknown}"
}

describe_rc() {
  case "$1" in
    124) echo "timed out after ${DRIVER_TIMEOUT_SEC}s" ;;
    137) echo "killed after ${DRIVER_TIMEOUT_SEC}s" ;;
    *) echo "rc=$1" ;;
  esac
}

report() {
  local head blob recent
  echo "deploy driver launcher: $SELF"
  if [ -f "$DEPLOY_DIR/$LAUNCHER_REL" ]; then
    if cmp -s "$SELF" "$DEPLOY_DIR/$LAUNCHER_REL"; then
      echo "  launcher: current (matches the deploy clone's copy)"
    else
      echo "  launcher: OUT OF DATE, the deploy clone's copy differs. Re-run: bash $DEPLOY_DIR/scripts/linux/install-deploy-sync.sh"
    fi
  fi
  head="$(clone_git rev-parse --verify -q 'HEAD^{commit}')"
  blob="$(clone_git rev-parse --verify -q "HEAD:$DRIVER_REL")"
  if [ -n "$blob" ]; then
    echo "  merged driver: blob ${blob:0:9}, deploy clone at ${head:0:9}"
  else
    echo "  merged driver: not found in the deploy clone at $DEPLOY_DIR"
  fi
  if [ -s "$LKG" ]; then
    echo "  last-known-good: blob $(lkg_field blob | cut -c1-9) from $(lkg_field commit | cut -c1-9), $(lkg_field how), $(lkg_field at)"
    if [ -n "$blob" ] && clone_git cat-file blob "$blob" | cmp -s - "$LKG"; then
      echo "    identical to the merged driver, so every fire runs merged code"
    else
      echo "    differs from the merged driver, which has not completed a clean pass here yet (see below)"
    fi
  else
    echo "  last-known-good: none yet. The merged driver's first clean pass creates it."
  fi
  recent="$(grep -F '[launcher]' "$LOG_FILE" 2>/dev/null | tail -n 5)"
  if [ -n "$recent" ]; then
    echo "  recent launcher decisions:"
    printf '%s\n' "$recent" | sed 's/^/    /'
  fi
  return 0
}

for arg in "$@"; do
  case "$arg" in
    --report) report; exit 0 ;;
    --status)
      # Read-only. The driver's --status includes this launcher's --report,
      # and any copy of the driver can print it: prefer the deploy clone's.
      if bash -n "$DEPLOY_DIR/$DRIVER_REL" 2>/dev/null; then exec bash "$DEPLOY_DIR/$DRIVER_REL" --status; fi
      if [ -s "$LKG" ]; then exec bash "$LKG" --status; fi
      report; exit 0 ;;
  esac
done

if ! mkdir -p "$STATE_DIR"; then
  log "cannot create $STATE_DIR; no driver can run" ERROR
  exit 1
fi

# One pass at a time. The timer never overlaps itself (systemd will not start a
# oneshot that is still running), but a hand run can, and two passes would race
# on the staged copy and on the containers. Driver runs close this fd, so
# nothing they leave behind can hold the lock.
if ! command -v flock >/dev/null 2>&1; then
  log "flock is not installed; running without the one-pass-at-a-time lock" WARN
elif ! exec 9>>"$LOCK_FILE"; then
  log "cannot open $LOCK_FILE; running without the one-pass-at-a-time lock" WARN
elif ! flock -n 9; then
  log "another deploy-sync pass holds $LOCK_FILE; not starting a second one" WARN
  exit 0
fi

drift_note=""
if [ -f "$DEPLOY_DIR/$LAUNCHER_REL" ] && ! cmp -s "$SELF" "$DEPLOY_DIR/$LAUNCHER_REL"; then
  drift_note="launcher out of date: the deploy clone's copy differs from $SELF, re-run scripts/linux/install-deploy-sync.sh"
  log "$drift_note" WARN
fi

have_lkg=0
lkg_commit="$(lkg_field commit)"
if [ -s "$LKG" ]; then
  if bash -n "$LKG" 2>/dev/null; then
    have_lkg=1
  else
    log "the last-known-good copy at $LKG fails bash -n; ignoring it" ERROR
  fi
fi

cand_commit=""; cand_blob=""; cand_problem=""
stage_candidate() {
  local err
  cand_commit="$(clone_git rev-parse --verify -q 'HEAD^{commit}')"
  if [ -z "$cand_commit" ]; then
    cand_problem="the deploy clone at $DEPLOY_DIR is not a readable git checkout"; return 1
  fi
  cand_blob="$(clone_git rev-parse --verify -q "$cand_commit:$DRIVER_REL")"
  if [ -z "$cand_blob" ]; then
    cand_problem="the deploy clone at ${cand_commit:0:9} has no $DRIVER_REL"; return 1
  fi
  if ! { clone_git cat-file blob "$cand_blob" > "$STAGED.tmp" \
         && chmod 0755 "$STAGED.tmp" && mv -f "$STAGED.tmp" "$STAGED"; }; then
    rm -f "$STAGED.tmp"
    cand_problem="could not stage $DRIVER_REL from ${cand_commit:0:9} into $STAGED"; return 1
  fi
  if ! err="$(bash -n "$STAGED" 2>&1)"; then
    # First line only, and no backslash: the reason ends up inside the status
    # file's JSON string.
    cand_problem="the merged driver from ${cand_commit:0:9} fails bash -n ($(printf '%s' "$err" | head -n 1 | tr -d '\\'))"
    return 1
  fi
  return 0
}

run_driver() { # run_driver <file> <kind> <commit> <note> [driver args...]
  local file="$1" kind="$2" commit="$3" note="$4"; shift 4
  DEPLOY_SYNC_DRIVER="$kind" DEPLOY_SYNC_DRIVER_COMMIT="$commit" DEPLOY_SYNC_DRIVER_NOTE="$note" \
    timeout --kill-after=30 "$DRIVER_TIMEOUT_SEC" bash "$file" "$@" 9>&-
}

run_last_known_good() { # run_last_known_good <why> [driver args...]
  local why="$1" rc=0; shift
  log "FALLBACK: $why; running the last-known-good copy (from ${lkg_commit:0:9})" ERROR
  run_driver "$LKG" last-known-good "$lkg_commit" \
    "fallback: $why; this pass ran the last-known-good driver from ${lkg_commit:0:9}${drift_note:+; $drift_note}" \
    "$@" || rc=$?
  log "last-known-good pass finished ($(describe_rc "$rc"))"
  return "$rc"
}

# FETCH_HEAD is rewritten by every fetch, and emptied by a failed one.
fetch_head_stamp() {
  local git_dir
  git_dir="$(clone_git rev-parse --absolute-git-dir)" || { echo none; return; }
  stat -c %.9Y "$git_dir/FETCH_HEAD" 2>/dev/null || echo none
}
fetched_since() { # fetched_since <stamp from before the run>: a fetch ran, and it succeeded
  [ "$(fetch_head_stamp)" != "$1" ] && clone_git rev-parse --verify -q 'FETCH_HEAD^{commit}' >/dev/null
}
reached_origin() { # reached_origin <stamp>: it fetched, and the clone is on what it fetched
  fetched_since "$1" || return 1
  [ "$(clone_git rev-parse --verify -q 'FETCH_HEAD^{commit}')" = "$(clone_git rev-parse --verify -q 'HEAD^{commit}')" ]
}

promote() { # keep the staged bytes that just completed a clean pass
  cp "$STAGED" "$LKG.tmp" && chmod 0755 "$LKG.tmp" && mv -f "$LKG.tmp" "$LKG" || return 1
  printf 'commit=%s\nblob=%s\nhow=%s\nat=%s\n' "$cand_commit" "$cand_blob" \
    "promoted after a clean pass" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LKG_META.tmp" \
    && mv -f "$LKG_META.tmp" "$LKG_META"
}

if ! stage_candidate; then
  if [ "$have_lkg" = 1 ]; then
    run_last_known_good "$cand_problem" "$@"; exit $?
  fi
  log "$cand_problem, and there is no last-known-good copy to run instead" ERROR
  exit 1
fi

if [ "$have_lkg" = 1 ] && cmp -s "$STAGED" "$LKG"; then
  # The steady state: merged == last-known-good, so there is nothing to judge.
  rc=0; run_driver "$STAGED" merged "$cand_commit" "$drift_note" "$@" || rc=$?
  exit "$rc"
fi

if [ "$have_lkg" = 1 ]; then
  log "running the merged driver from ${cand_commit:0:9}; it differs from the last-known-good copy (from ${lkg_commit:0:9}) and replaces it if this pass completes cleanly"
else
  log "no last-known-good copy yet; running the merged driver from ${cand_commit:0:9}, whose first clean pass becomes it" WARN
fi
before="$(fetch_head_stamp)"
rc=0; run_driver "$STAGED" merged "$cand_commit" "$drift_note" "$@" || rc=$?

if reached_origin "$before"; then
  if [ "$rc" = 0 ]; then
    if promote; then
      log "promoted the merged driver from ${cand_commit:0:9} to last-known-good"
    else
      log "the merged driver from ${cand_commit:0:9} completed a clean pass, but $LKG could not be written" ERROR
    fi
  else
    log "the merged driver from ${cand_commit:0:9} failed after reaching $SOURCE_REMOTE/$SYNC_BRANCH ($(describe_rc "$rc")). Not promoting it; no fallback either, because the clone is current and a fix will still arrive." WARN
  fi
  exit "$rc"
fi
if [ "$rc" = 0 ] && fetched_since "$before"; then
  log "the merged driver from ${cand_commit:0:9} exited cleanly without moving the clone (a deferral); it is judged again next fire"
  exit 0
fi

why="the merged driver from ${cand_commit:0:9} did not reach $SOURCE_REMOTE/$SYNC_BRANCH ($(describe_rc "$rc"))"
[ "$rc" = 0 ] && rc=1   # exited 0 without a successful fetch: no pass happened
if [ "$have_lkg" != 1 ]; then
  log "$why, and there is no last-known-good copy to fall back to" ERROR
  exit "$rc"
fi
if ! timeout 30 git -C "$DEPLOY_DIR" ls-remote --exit-code "$SOURCE_REMOTE" "refs/heads/$SYNC_BRANCH" >/dev/null 2>&1; then
  log "$why, and $SOURCE_REMOTE/$SYNC_BRANCH does not answer, so the last-known-good copy could not reach it either. Not falling back; the next fire retries." WARN
  exit "$rc"
fi
run_last_known_good "$why" "$@"; exit $?
