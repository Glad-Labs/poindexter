#!/usr/bin/env bash
# run-session.sh <session-name> — Linux port of the Windows claude-sessions.ps1
# Run-Session. Runs a deterministic/local-LLM ops session (the 7 enabled ones),
# with git-worktree isolation for the sessions that commit + open PRs.
set -euo pipefail
NAME="${1:?session name required}"
WORK="${POINDEXTER_REPO:-$HOME/glad-labs-website}"
MAINPKG="$WORK/src/cofounder_agent"
WT_ROOT="$HOME/.poindexter/worktrees"
LOGDIR="$HOME/.poindexter/logs/claude-sessions"
mkdir -p "$LOGDIR" "$WT_ROOT"
STAMP="$(date +%Y-%m-%d-%H%M)"
LOG="$LOGDIR/$NAME-$STAMP.log"
# Open the log BEFORE the first git call. Everything below redirects into it, so
# a setup failure leaves a log file rather than only a journald line — the 2026-08-14
# test-health fetch died two seconds in and wrote no log at all, because the
# redirect used to start at `worktree add`.
echo "=== session $NAME starting $(date -Is) ===" >>"$LOG"

# Bounded retry for the one setup step that touches the network. A transient
# GitHub 5xx on fetch used to abort the whole run, and because systemd unit
# state is a latch (not an event) that painted the Grafana panel red until the
# next day's fire — 24h of red for a blip that cleared in seconds.
FETCH_ATTEMPTS="${OPS_GIT_FETCH_ATTEMPTS:-3}"
FETCH_RETRY_SECONDS="${OPS_GIT_FETCH_RETRY_SECONDS:-15}"
CHECKOUT_SYNC="${OPS_CHECKOUT_SYNC:-1}"

fetch_origin() {
  local attempt=1 delay
  while :; do
    if git -C "$WORK" fetch origin --quiet >>"$LOG" 2>&1; then
      return 0
    fi
    if [ "$attempt" -ge "$FETCH_ATTEMPTS" ]; then
      echo "git fetch origin failed after $attempt attempt(s) — aborting" >>"$LOG"
      return 1
    fi
    delay=$(( FETCH_RETRY_SECONDS * attempt ))     # linear backoff: 15s, 30s, …
    echo "git fetch origin failed (attempt $attempt/$FETCH_ATTEMPTS) — retrying in ${delay}s" >>"$LOG"
    sleep "$delay"
    attempt=$(( attempt + 1 ))
  done
}

# Sessions that commit run in an isolated worktree off fresh origin/main;
# read/act-via-API sessions run from the shared checkout.
case "$NAME" in
  codebase-audit|doc-sync|claude-md-sync|test-health) NEEDS_WT=1 ;;
  *) NEEDS_WT=0 ;;
esac

# Pre-flight self-update: fast-forward the shared checkout to origin/main.
# Nothing else deploys this tree — poindexter-deploy-sync serves the dedicated
# deploy clone and must NEVER point here (its step 4 is reset --hard + clean,
# and this checkout holds Matt's stashes and agent worktrees). Found 2026-08-15:
# PR #3228's fetch retry was merged but the deployed wrapper still ran the old
# code, 3 commits behind. ff-only is the whole point — merge, never reset.
#
# Guards: only on main, only with clean *tracked* files (-uno: a stray
# untracked scratch file must not wedge the sync forever), fail-safe on
# unparseable behind-counts (skip, don't merge). Any skip or failure is a log
# line, never an abort — a stale wrapper is the status quo, not a new failure.
#
# Mid-run safety: git REPLACES files (new inode) rather than truncating in
# place, so the bash instance running this script keeps reading its old fd
# untouched; a merged wrapper change takes effect on the NEXT timer fire. The
# shared-checkout session payloads (scripts/ops_sessions/*.py for the
# non-worktree sessions) load after this point, so they run current in THIS run.
DID_FETCH=0
sync_shared_checkout() {
  if [ "$CHECKOUT_SYNC" != "1" ]; then
    echo "checkout sync disabled (OPS_CHECKOUT_SYNC=$CHECKOUT_SYNC)" >>"$LOG"
    return 0
  fi
  local branch behind
  branch="$(git -C "$WORK" symbolic-ref --quiet --short HEAD || true)"
  if [ "$branch" != "main" ]; then
    echo "checkout sync skipped: $WORK is on '${branch:-detached}', not main" >>"$LOG"
    return 0
  fi
  if [ -n "$(git -C "$WORK" status --porcelain -uno 2>>"$LOG")" ]; then
    echo "checkout sync skipped: $WORK has uncommitted tracked changes (never merged onto)" >>"$LOG"
    return 0
  fi
  if ! fetch_origin; then
    echo "checkout sync skipped: fetch failed — wrapper may be stale" >>"$LOG"
    return 0
  fi
  DID_FETCH=1
  behind="$(git -C "$WORK" rev-list --count HEAD..origin/main 2>/dev/null | tr -d '[:space:]')"
  case "$behind" in
    ''|*[!0-9]*) behind=0 ;;
  esac
  if [ "$behind" = "0" ]; then
    return 0
  fi
  if git -C "$WORK" merge --ff-only origin/main >>"$LOG" 2>&1; then
    echo "checkout sync: fast-forwarded $WORK $behind commit(s) to origin/main (this run stays on the pre-update wrapper; next fire runs the new one)" >>"$LOG"
  else
    echo "checkout sync WARNING: $WORK is $behind commit(s) behind origin/main but cannot fast-forward (diverged, or main checked out in a worktree) — wrapper stays STALE until reconciled by hand" >>"$LOG"
  fi
  return 0
}
sync_shared_checkout

RUNDIR="$WORK"; BRANCH=""; WT=""
if [ "$NEEDS_WT" = 1 ]; then
  git -C "$WORK" worktree prune >>"$LOG" 2>&1
  if [ "$DID_FETCH" != 1 ] && ! fetch_origin; then
    exit 1
  fi
  BRANCH="auto/$NAME-$STAMP"
  WT="$WT_ROOT/$NAME-$STAMP"
  if ! git -C "$WORK" worktree add -b "$BRANCH" "$WT" origin/main >>"$LOG" 2>&1; then
    echo "worktree add failed — aborting (refusing to run in the shared checkout)" >>"$LOG"
    exit 1
  fi
  # The husky/lint-staged pre-commit hook needs node_modules; symlink the shared
  # one in (read-shared, instant). Removed in cleanup before the worktree goes.
  ln -s "$WORK/node_modules" "$WT/node_modules" 2>/dev/null || true
  RUNDIR="$WT"
fi

cleanup() {
  if [ "$NEEDS_WT" = 1 ]; then
    rm -f "$WT/node_modules"                       # unlink only — never follows into shared
    git -C "$WORK" worktree remove "$WT" --force || true
    git -C "$WORK" branch -D "$BRANCH" || true
    git -C "$WORK" worktree prune || true
  fi
}
trap cleanup EXIT

SCRIPT="$RUNDIR/scripts/ops_sessions/${NAME//-/_}.py"
# NOTE: the CWD here is the *shared* checkout's package dir (that's where the
# poetry env lives) while NEEDS_WT sessions commit inside $WT. Session scripts
# must therefore pass an explicit cwd to every git/gh call — a bare `gh pr
# create` reads this CWD's branch, not the worktree's. See _common.gh().
RC=0
( cd "$MAINPKG" && poetry run python "$SCRIPT" ) >>"$LOG" 2>&1 || RC=$?
echo "session $NAME complete (rc=$RC)" >>"$LOG"
exit "$RC"
