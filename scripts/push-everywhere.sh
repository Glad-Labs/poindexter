#!/usr/bin/env bash
# ==============================================================================
# push-everywhere.sh — push to origin AND refresh the public github mirror
#
# Single command that:
#   1. Pushes current branch to origin (= Glad-Labs/glad-labs-stack, full tree)
#   2. Runs scripts/sync-to-github.sh to refresh Glad-Labs/poindexter (public
#      OSS subset, filtered by the script)
#
# Use instead of `git push origin main` when you want both repos updated.
# Wired up as `git pushe` alias by scripts/install-git-hooks.sh.
#
# Usage: bash scripts/push-everywhere.sh
# Env:   SKIP_GITHUB_SYNC=1 → skip step 2 (mirrors `git push origin main` only)
# ==============================================================================

set -euo pipefail

BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$BRANCH" != "main" ]]; then
  echo "[push-everywhere] Current branch is '$BRANCH', not 'main'."
  echo "[push-everywhere] github sync only happens for main. Pushing to origin only."
  git push origin "$BRANCH" "$@"
  exit 0
fi

echo "[push-everywhere] Step 1/2 — pushing main → origin (Glad-Labs/glad-labs-stack)..."
git push origin main "$@"

if [[ "${SKIP_GITHUB_SYNC:-0}" == "1" ]]; then
  echo "[push-everywhere] SKIP_GITHUB_SYNC=1 — done after step 1."
  exit 0
fi

echo "[push-everywhere] Step 2/2 — syncing filtered subset → github (Glad-Labs/poindexter)..."
bash "$(dirname "$0")/sync-to-github.sh"

echo "[push-everywhere] Done. Both remotes updated."
