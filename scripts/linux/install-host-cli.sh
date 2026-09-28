#!/usr/bin/env bash
# install-host-cli.sh — make the host `poindexter` command run the DEPLOYED code
# (Glad-Labs/glad-labs-stack#4156).
#
# One-time host setup; safe to re-run. It:
#   1. builds the CLI venv (~/.poindexter/cli-venv) from the deploy clone's
#      poetry.lock, editable-installing the clone's `poindexter` package
#      (scripts/linux/cli-venv-sync.sh --force);
#   2. points ~/.local/bin/poindexter at the deploy clone's launcher,
#      scripts/linux/poindexter-cli.sh, as a symlink. A regular file already
#      there (the hand-written launcher this replaces) is kept, non-executable,
#      as poindexter.pre-host-cli-<timestamp>;
#   3. proves the result: `poindexter --help` runs, and the package it imports
#      is the deploy clone's.
#
# Nothing needs re-running afterwards. The launcher, the sync logic and the CLI
# code are all read from the deploy clone on each call, and
# deploy-checkout-sync.sh keeps the venv's dependencies on the deployed
# lockfile (the launcher also re-syncs on demand if a lockfile change beats it
# there).
#
# Usage: bash scripts/linux/install-host-cli.sh
# Env:   POINDEXTER_DEPLOY_ROOT, POINDEXTER_CLI_VENV (shared with the launcher
#        and cli-venv-sync.sh), POINDEXTER_CLI_BIN_DIR (default ~/.local/bin).
set -euo pipefail

DEPLOY_ROOT="${POINDEXTER_DEPLOY_ROOT:-$HOME/.poindexter/deploy/glad-labs-stack}"
VENV="${POINDEXTER_CLI_VENV:-$HOME/.poindexter/cli-venv}"
BIN_DIR="${POINDEXTER_CLI_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$DEPLOY_ROOT/scripts/linux/poindexter-cli.sh"
SYNC="$DEPLOY_ROOT/scripts/linux/cli-venv-sync.sh"
TARGET="$BIN_DIR/poindexter"

log() { printf '[install-host-cli] %s\n' "$*"; }
die() { printf '[install-host-cli] ERROR: %s\n' "$*" >&2; exit 1; }

git -C "$DEPLOY_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "no deploy clone at $DEPLOY_ROOT — create it with scripts/setup-deploy-checkout.sh"
if [ ! -f "$LAUNCHER" ] || [ ! -f "$SYNC" ]; then
  die "the deploy clone at $DEPLOY_ROOT predates the host-CLI launcher (no $LAUNCHER). Bring it to origin/main first (systemctl start poindexter-deploy-sync.service), then re-run."
fi

log "Building the CLI environment at $VENV from $DEPLOY_ROOT…"
POINDEXTER_DEPLOY_ROOT="$DEPLOY_ROOT" POINDEXTER_CLI_VENV="$VENV" bash "$SYNC" --force \
  || die "building the CLI environment failed — see ~/.poindexter/cli-venv-sync.log"

mkdir -p "$BIN_DIR"
if [ -L "$TARGET" ]; then
  current="$(readlink "$TARGET")"
  if [ "$current" = "$LAUNCHER" ]; then
    log "$TARGET already links to the launcher"
  else
    ln -sfn "$LAUNCHER" "$TARGET"
    log "repointed $TARGET -> $LAUNCHER (was -> $current)"
  fi
elif [ -e "$TARGET" ]; then
  backup="$TARGET.pre-host-cli-$(date +%Y%m%d%H%M%S)"
  mv "$TARGET" "$backup"
  chmod a-x "$backup" 2>/dev/null || true
  ln -s "$LAUNCHER" "$TARGET"
  log "linked $TARGET -> $LAUNCHER (previous launcher kept as $backup)"
else
  ln -s "$LAUNCHER" "$TARGET"
  log "linked $TARGET -> $LAUNCHER"
fi

# Things that would make the operator's shell run something else.
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) log "WARNING: $BIN_DIR is not on PATH — add it, or 'poindexter' will not resolve to $TARGET" ;;
esac
for rc in "$HOME/.bashrc" "$HOME/.bash_aliases" "$HOME/.profile" "$HOME/.zshrc"; do
  if [ -f "$rc" ] && grep -Eq '^[[:space:]]*(alias[[:space:]]+poindexter=|(function[[:space:]]+)?poindexter[[:space:]]*\(\))' "$rc"; then
    log "WARNING: $rc defines a 'poindexter' alias or function, which shadows $TARGET in interactive shells — remove it"
  fi
done

POINDEXTER_DEPLOY_ROOT="$DEPLOY_ROOT" POINDEXTER_CLI_VENV="$VENV" "$TARGET" --help >/dev/null \
  || die "'$TARGET --help' failed"
imports="$(cd / && env -u PYTHONPATH -u PYTHONHOME "$VENV/bin/python" -c \
  'import os, poindexter; print(os.path.dirname(os.path.realpath(poindexter.__file__)))')"
want="$(cd "$DEPLOY_ROOT/src/cofounder_agent/poindexter" && pwd -P)"
[ "$imports" = "$want" ] || die "the CLI imports poindexter from $imports, not the deploy clone ($want)"
log "OK — 'poindexter' runs the deploy clone's code ($imports) at $(git -C "$DEPLOY_ROOT" rev-parse --short HEAD)"
