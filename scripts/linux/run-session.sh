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

# Sessions that commit run in an isolated worktree off fresh origin/main;
# read/act-via-API sessions run from the shared checkout.
case "$NAME" in
  codebase-audit|doc-sync|claude-md-sync|test-health) NEEDS_WT=1 ;;
  *) NEEDS_WT=0 ;;
esac

RUNDIR="$WORK"; BRANCH=""; WT=""
if [ "$NEEDS_WT" = 1 ]; then
  git -C "$WORK" worktree prune
  git -C "$WORK" fetch origin --quiet
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
( cd "$MAINPKG" && poetry run python "$SCRIPT" ) >>"$LOG" 2>&1
echo "session $NAME complete" >>"$LOG"
