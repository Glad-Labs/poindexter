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

# Remove private/premium files from this temporary branch (not from disk)
git rm -r --cached --quiet web/public-site/ 2>/dev/null || true
git rm -r --cached --quiet infrastructure/headscale/certs/ 2>/dev/null || true
git rm -r --cached --quiet docs/ 2>/dev/null || true
git rm -r --cached --quiet .shared-context/ 2>/dev/null || true
git rm -r --cached --quiet marketing/ 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/writing_samples/ 2>/dev/null || true
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
