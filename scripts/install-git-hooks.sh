#!/usr/bin/env bash
# ==============================================================================
# install-git-hooks.sh — wire up local git hooks + the `git pushe` alias
#
# Run once after cloning (or after the hook directory layout changes).
# Idempotent — safe to re-run.
#
# What it sets up:
#   1. core.hooksPath = .githooks  (so git uses .githooks/* instead of .git/hooks/*)
#   2. alias.pushe    = !bash scripts/push-everywhere.sh
#       Usage: `git pushe` → push origin + sync filtered subset to github
#
# Usage: bash scripts/install-git-hooks.sh
# ==============================================================================

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "Setting core.hooksPath = .githooks ..."
git config core.hooksPath .githooks

# Make the .githooks/* files executable
chmod +x .githooks/* 2>/dev/null || true
chmod +x scripts/push-everywhere.sh scripts/sync-to-github.sh 2>/dev/null || true

echo "Adding 'git pushe' alias → bash scripts/push-everywhere.sh ..."
git config alias.pushe '!bash scripts/push-everywhere.sh'

echo ""
echo "Done. Going forward:"
echo "  git pushe              → push to origin (full tree) + sync filtered subset to github"
echo "  git pushe --force      → same with --force on the origin push"
echo "  SKIP_GITHUB_SYNC=1 git pushe   → push to origin only"
echo "  git push origin main   → push to origin only (no auto-sync; useful for in-progress work)"
echo ""
echo "Or run manually anytime:"
echo "  bash scripts/sync-to-github.sh"
