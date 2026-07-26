#!/usr/bin/env bash
# ==============================================================================
# push-everywhere.sh — push to origin AND refresh the public github mirror
#
# Status as of 2026-04-30: this script is now a FALLBACK. The primary sync path
# is the GitHub Actions workflow `.github/workflows/sync-to-public-poindexter.yml`
# which runs automatically on every push to glad-labs-stack/main and mirrors
# the filtered subset to Glad-Labs/poindexter via the POINDEXTER_DEPLOY_KEY
# secret. You normally just `git push origin main` and CI does the rest in ~30s.
#
# Use this script (or `git pushe`) when you want IMMEDIATE local sync without
# waiting for CI — useful if CI is broken, if Actions minutes are exhausted,
# or when iterating on the sync filter itself and you want fast feedback.
#
# Single command that:
#   1. Pushes current branch to origin (= Glad-Labs/glad-labs-stack, full tree)
#   2. Runs scripts/sync-to-github.sh locally to refresh Glad-Labs/poindexter
#      (public OSS subset, filtered by the script)
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
