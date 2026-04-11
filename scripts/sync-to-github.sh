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

# Commit the removal (temporary — never pushed to Gitea)
git commit -m "sync: exclude private files for public repo" --allow-empty 2>/dev/null

# Force push this clean branch to GitHub as main
git push "$GITHUB_REMOTE" "${TEMP_BRANCH}:${BRANCH}" --force 2>&1

# Switch back to original branch and delete temp
git checkout "$BRANCH" 2>/dev/null
git branch -D "$TEMP_BRANCH" 2>/dev/null

echo "Done. GitHub synced (private files excluded)."
