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

RUNDIR="$WORK"; BRANCH=""; WT=""
if [ "$NEEDS_WT" = 1 ]; then
  git -C "$WORK" worktree prune >>"$LOG" 2>&1
  if ! fetch_origin; then
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
