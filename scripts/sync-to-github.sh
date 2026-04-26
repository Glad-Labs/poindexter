#!/usr/bin/env bash
# ==============================================================================
# Sync to GitHub Public Repo
#
# Produces a clean public history by stripping private paths from EVERY commit
# (not just HEAD) using git-filter-repo, then force-pushing to the public repo.
#
# Why filter-repo, not `git rm --cached`:
#   The previous version rm'd private files from the working tree of a temp
#   branch, committed the removal, and force-pushed. Old commits in the public
#   history still contained the stripped content — anyone could `git checkout
#   <old-sha>` on the public repo and read it. This script rewrites every
#   commit so the private paths never appear in any reachable history.
#
# Requirements: git-filter-repo on PATH (`pip install git-filter-repo`).
#
# Usage: ./scripts/sync-to-github.sh
# ==============================================================================

set -euo pipefail

GITHUB_REMOTE_URL="https://github.com/Glad-Labs/poindexter.git"
SOURCE_DIR="$(git rev-parse --show-toplevel)"
WORK_DIR="$(mktemp -d -t poindexter-sync-XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "ERROR: git-filter-repo not found on PATH. Install with: pip install git-filter-repo" >&2
  exit 1
fi

echo "Syncing to GitHub via history rewrite..."
echo "  source : $SOURCE_DIR"
echo "  work   : $WORK_DIR"

# Mirror-clone the local repo (preserves all branches and tags).
git clone --mirror "$SOURCE_DIR" "$WORK_DIR/repo.git" >/dev/null

# Paths stripped from every commit and every branch/tag in the public repo.
# Keep this list authoritative — anything that should never appear in public
# git history goes here.
#
# Categories:
#   - Branded surfaces (frontend, storefront, marketing copy, brand screenshots)
#   - Private operator UI (oversight-hub) and operator MCP server
#   - Personal context (CLAUDE.md, writing-style training data)
#   - Private infrastructure (headscale VPN config + certs)
#   - Operator-only configs (.continue/, gladlabs-mcp configs in archive)
#   - Test/dev artifacts that leak operator UI screenshots (.playwright-mcp/)

cd "$WORK_DIR/repo.git"
git filter-repo --force \
  --invert-paths \
  --path web/storefront \
  --path web/public-site \
  --path web/oversight-hub \
  --path mcp-server-gladlabs \
  --path marketing \
  --path docs/marketing \
  --path docs/brand \
  --path docs/components/public-site \
  --path docs/components/oversight-hub \
  --path docs/archive-active/root-cleanup-feb2026 \
  --path docs/archive-old/docs-violations/next-js-public-site.md \
  --path docs/archive-old/docs-violations/react-oversight-hub.md \
  --path infrastructure/headscale \
  --path src/cofounder_agent/writing_samples \
  --path src/cofounder_agent/web/public-site \
  --path .archive/cleanup-feb2026 \
  --path .continue \
  --path .playwright-mcp \
  --path .shared-context \
  --path archive/gladlabs-mcp-config.yaml \
  --path archive/gladlabs-mcp-server.yaml \
  --path skills/openclaw/gladlabs-config.json \
  --path scripts/setup-headscale.ps1 \
  --path scripts/fix-public-site.sh \
  --path public-site-checklist.sh \
  --path migrations/dev_writing_samples.sql \
  --path migrations/dev_writing_samples_export.csv \
  --path dev_writing_samples.sql \
  --path dev_writing_samples_export.csv \
  --path docker-compose.local.yml \
  --path .env.example \
  --path .gitleaks-baseline.json \
  --path .woodpecker.yml \
  --path CLAUDE.md \
  --path-glob "public-site-*.png" \
  --path-glob "*-oversight-hub-*.png" \
  --path-glob "0?-oversight-hub-*.png" \
  --path-glob "oversight-hub-*.png" \
  --path-glob "oversight-hub-*.md" \
  --path infrastructure/grafana/dashboards/approval-queue.json \
  --path infrastructure/grafana/dashboards/cost-analytics.json \
  --path infrastructure/grafana/dashboards/infrastructure-data.json \
  --path infrastructure/grafana/dashboards/link-registry.json \
  --path infrastructure/grafana/dashboards/quality-content.json \
  --path infrastructure/grafana/dashboards/qa-observability.json \
  --path infrastructure/grafana/dashboards/system-health.json

# filter-repo strips remotes by design; re-add the public remote and push.
git remote add origin "$GITHUB_REMOTE_URL"
echo "Force-pushing rewritten history to $GITHUB_REMOTE_URL..."
git push --force --mirror origin

echo "Done. Public repo synced with private paths stripped from history."
