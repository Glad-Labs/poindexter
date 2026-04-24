#!/usr/bin/env bash
# ==============================================================================
# Sync to GitHub Public Repo
#
# Copies the current branch to GitHub, EXCLUDING private files:
#   - web/public-site/ (branded frontend)
#   - infrastructure/headscale/ (VPN config)
#   - .env* (secrets)
#
# Usage: ./scripts/sync-to-github.sh
# ==============================================================================

set -euo pipefail

GITHUB_REMOTE="github"
BRANCH="main"

echo "Syncing to GitHub (excluding private files)..."

# Create a temporary branch with private files removed
TEMP_BRANCH="github-sync-temp-$$"
git checkout -b "$TEMP_BRANCH" 2>/dev/null

# Remove private/premium files from this temporary branch (not from disk).
# Everything listed here is either a secret, private infrastructure, Glad Labs
# operator-only tooling, or a file that leaks personal context (bank balance,
# memory paths, internal URLs). Nothing here should ship to the public repo.

# === Premium product surfaces and private branding ===
git rm -r --cached --quiet web/public-site/ 2>/dev/null || true              # branded Next.js site
git rm -r --cached --quiet web/storefront/ 2>/dev/null || true               # gladlabs.ai storefront (Lemon Squeezy checkout + copy)
git rm -r --cached --quiet docs/ 2>/dev/null || true                         # internal docs
git rm -r --cached --quiet marketing/ 2>/dev/null || true                    # marketing materials
git rm -r --cached --quiet src/cofounder_agent/writing_samples/ 2>/dev/null || true  # private writing style training data
git rm -r --cached --quiet mcp-server-gladlabs/ 2>/dev/null || true          # private operator MCP server

# === Private infrastructure (Matt's local setup, not useful publicly) ===
git rm --cached --quiet .woodpecker.yml 2>/dev/null || true                  # internal Gitea CI config
git rm --cached --quiet scripts/migrate-poindexter-rename.sh 2>/dev/null || true  # one-shot rebrand script, specific to Matt's install

# === Personal context (bank balance, memory paths, internal URLs) ===
git rm --cached --quiet CLAUDE.md 2>/dev/null || true                        # Matt's personal Claude Code instructions

# === Private session state and build artifacts ===
git rm -r --cached --quiet .shared-context/ 2>/dev/null || true              # cross-session handoff notes
git rm --cached --quiet src/cofounder_agent/.coverage 2>/dev/null || true    # pytest-cov SQLite artifact

# === GitHub Actions internals not needed by public consumers ===
git rm --cached --quiet .github/COMMIT_MESSAGE_*.txt 2>/dev/null || true
git rm --cached --quiet .github/create-tech-debt-issues.sh 2>/dev/null || true
git rm --cached --quiet .github/tech-debt-issues.json 2>/dev/null || true
git rm -r --cached --quiet .github/workflows-disabled/ 2>/dev/null || true
git rm --cached --quiet .github/workflows/ci.yml 2>/dev/null || true          # Deploy runs from glad-labs-stack, not poindexter

# === Operator-specific files (Glad Labs internal, not customer-facing) ===
git rm --cached --quiet docker-compose.local.yml 2>/dev/null || true          # Matt's full stack with Gitea, pgAdmin, SDXL, etc.
git rm --cached --quiet .env.example 2>/dev/null || true                      # Legacy; customers use poindexter setup

# === Premium Grafana dashboards (Poindexter Pro — keep only pipeline-operations free) ===
git rm --cached --quiet infrastructure/grafana/dashboards/approval-queue.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/cost-analytics.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/infrastructure-data.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/link-registry.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/quality-content.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/qa-observability.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/system-health.json 2>/dev/null || true

# === Local gitleaks baseline — operator-specific, regenerated per clone ===
# Contains historical commit hashes + file paths where gitleaks flagged
# known-false-positive secrets. Reveals repo history structure + author
# emails; every operator should generate their own baseline anyway.
git rm --cached --quiet .gitleaks-baseline.json 2>/dev/null || true

# Commit the removal (temporary — never pushed to Gitea)
git commit -m "sync: exclude private files for public repo" --allow-empty 2>/dev/null

# Force push this clean branch to GitHub as main
git push "$GITHUB_REMOTE" "${TEMP_BRANCH}:${BRANCH}" --force 2>&1

# Switch back to original branch and delete temp.
# Force-checkout: the temp branch has lots of files removed from the index via
# `git rm --cached` above; without -f, git refuses to overwrite what it now sees
# as untracked working-tree files — even though they're identical to $BRANCH's
# tracked versions. Dropping -f left the script stuck on the temp branch (set -e
# exits before `branch -D` runs). -f is safe here because working tree content
# matches $BRANCH's tree exactly (only the index was mutated).
git checkout -f "$BRANCH"
git branch -D "$TEMP_BRANCH"

echo "Done. GitHub synced (private files excluded)."
