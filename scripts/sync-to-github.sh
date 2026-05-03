#!/usr/bin/env bash
# ==============================================================================
# Sync to GitHub Public Repo (Glad-Labs/poindexter)
#
# Copies the current branch (full tree, lives on origin = glad-labs-stack)
# to the public Glad-Labs/poindexter mirror, EXCLUDING all private files:
# branded surfaces (web/public-site, web/storefront), operator MCP server,
# premium dashboards, marketing materials, writing samples, secrets,
# personal Claude config, internal docs, etc. — see filter list below.
#
# Workflow as of 2026-04-30 (post-gitea decommission):
#   - Develop on local main; push to origin (glad-labs-stack/main)
#   - Periodically run THIS script to refresh the public mirror
#   - Public-only PRs (e.g. open-source contributions) can be opened
#     directly against github/main without going through this script
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
git rm -r --cached --quiet marketing/ 2>/dev/null || true                    # marketing materials
git rm -r --cached --quiet packages/ 2>/dev/null || true                     # @glad-labs/brand design tokens (consumed only by stripped surfaces)

# === Internal-only docs (the rest of docs/ ships to public for Mintlify) ===
# Strip individual subpaths instead of the whole tree — most of docs/ is
# operator-facing and gets hosted on Mintlify (see docs.json at repo root).
# These specific files are internal: incident audits, planning docs,
# session summaries, brand assets, launch drafts, brainstorming specs.
git rm -r --cached --quiet docs/brand/ 2>/dev/null || true                              # brand asset PNGs (logos, screenshots)
git rm -r --cached --quiet docs/experiments/ 2>/dev/null || true                        # launch drafts + tuning notes
git rm -r --cached --quiet docs/superpowers/ 2>/dev/null || true                        # internal brainstorming/specs/plans workflow
git rm --cached --quiet docs/operations/documentation-audit-2026-04-29.md 2>/dev/null || true
git rm --cached --quiet docs/operations/migrations-audit-2026-04-27.md 2>/dev/null || true
git rm --cached --quiet docs/operations/overnight-2026-04-27-summary.md 2>/dev/null || true
git rm --cached --quiet docs/operations/public-site-audit-2026-04-27.md 2>/dev/null || true
git rm --cached --quiet docs/operations/silent-failures-audit-2026-04-27.md 2>/dev/null || true
git rm --cached --quiet docs/operations/test-coverage-2026-04-27.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/database-and-embeddings-plan-2026-04-24.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/gh-107-secret-keys-audit-2026-04-24.md 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/writing_samples/ 2>/dev/null || true  # private writing style training data
git rm -r --cached --quiet mcp-server-gladlabs/ 2>/dev/null || true          # private operator MCP server

# === Private infrastructure (Matt's local setup, not useful publicly) ===
git rm --cached --quiet .woodpecker.yml 2>/dev/null || true                  # legacy Gitea CI config (unused post-decommission)
git rm -r --cached --quiet .gitea/ 2>/dev/null || true                       # gitea decommissioned; entire workflow folder is dead
git rm --cached --quiet scripts/migrate-poindexter-rename.sh 2>/dev/null || true  # one-shot rebrand script, specific to Matt's install
git rm --cached --quiet scripts/sync-to-github.sh 2>/dev/null || true        # the sync script itself — internal-only, exposes strip logic
git rm --cached --quiet scripts/push-everywhere.sh 2>/dev/null || true       # local-dev-only, two-remote push helper
git rm --cached --quiet scripts/install-git-hooks.sh 2>/dev/null || true     # local-dev-only, sets up the pushe alias
git rm --cached --quiet scripts/system-health-check.sh 2>/dev/null || true   # operator-specific health probes (Matt's install)
git rm --cached --quiet scripts/claude-sessions.ps1 2>/dev/null || true       # Windows scheduled-task setup for autonomous Claude sessions (Matt's install)
git rm --cached --quiet scripts/run-claude-session.cmd 2>/dev/null || true    # cmd wrapper for above
git rm --cached --quiet scripts/sync-premium-prompts.py 2>/dev/null || true   # syncs premium prompts from Glad Labs operator stash, no-op for public users

# === Operator-specific taps (read from Matt's local Claude / OpenClaw state) ===
git rm --cached --quiet src/cofounder_agent/services/taps/claude_code_sessions.py 2>/dev/null || true  # taps ~/.claude/projects/*.jsonl — Matt's actual conversations
git rm --cached --quiet src/cofounder_agent/tests/unit/services/test_claude_code_sessions_tap.py 2>/dev/null || true

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
git rm --cached --quiet .github/workflows/sync-to-public-poindexter.yml 2>/dev/null || true  # The mirror sync ITSELF lives only on glad-labs-stack — shipping it to the public mirror caused recursive runs that fail every time (no POINDEXTER_DEPLOY_KEY secret on the public side) and burn CI minutes

# === Operator-specific files (Glad Labs internal, not customer-facing) ===
git rm --cached --quiet docker-compose.local.yml 2>/dev/null || true          # Matt's full local stack with pgAdmin, SDXL, etc.

# === Operator OpenClaw skill toggles (private to Glad Labs install) ===
git rm --cached --quiet skills/openclaw/gladlabs-config.json 2>/dev/null || true
git rm --cached --quiet .env.example 2>/dev/null || true                      # Legacy; customers use poindexter setup

# === Premium Grafana dashboards (Seed Package — keep only pipeline-merged free) ===
git rm --cached --quiet infrastructure/grafana/dashboards/approval-queue.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/cost-analytics.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/infrastructure-data.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/link-registry.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/quality-content.json 2>/dev/null || true

# === Local gitleaks baseline — operator-specific, regenerated per clone ===
# Contains historical commit hashes + file paths where gitleaks flagged
# known-false-positive secrets. Reveals repo history structure + author
# emails; every operator should generate their own baseline anyway.
git rm --cached --quiet .gitleaks-baseline.json 2>/dev/null || true

# Commit the removal (temporary — only pushed to github, never to origin/glad-labs-stack)
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
