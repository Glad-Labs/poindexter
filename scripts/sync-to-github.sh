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

# Capture the mirror's current main SHA up front so we can lease against it on
# push (see --force-with-lease invocation near the end). If main has moved on
# the mirror between now and the push (i.e. someone DID push something), the
# push aborts loudly instead of silently overwriting their commit. See #392 +
# the e302fa5d post-mortem for why plain --force is unsafe here.
EXPECTED_MIRROR_SHA="$(git ls-remote "$GITHUB_REMOTE" "refs/heads/${BRANCH}" | awk '{print $1}')"
if [[ -z "$EXPECTED_MIRROR_SHA" ]]; then
  # First-ever push (mirror branch doesn't exist yet) — the empty lease value
  # tells git "expect this ref to NOT exist", which is the correct safety
  # check for a brand-new branch. See `git push --help` § force-with-lease.
  echo "[sync] Mirror $BRANCH does not exist yet; will create it on push."
fi
echo "[sync] Mirror $BRANCH expected SHA: ${EXPECTED_MIRROR_SHA:-<none>}"

# Create a temporary branch with private files removed
TEMP_BRANCH="github-sync-temp-$$"
git checkout -b "$TEMP_BRANCH" 2>/dev/null

# Remove private/premium files from this temporary branch (not from disk).
# Everything listed here is either a secret, private infrastructure, Glad Labs
# operator-only tooling, or a file that leaks personal context (bank balance,
# memory paths, internal URLs). Nothing here should ship to the public repo.

# === Repo-divergent configs that must be SWAPPED, not deleted ===
# release-please-config.json on glad-labs-stack lists web/public-site/package.json
# as an extra-file. That path is private — stripped below — so on the public
# mirror release-please can't find it and skips the entry. Keep a parallel
# release-please-config.poindexter.json checked in to glad-labs-stack with the
# public-safe extra-files list and swap it into place here so the public mirror
# ships a config whose every extra-file path actually exists in the public tree.
# Closes Glad-Labs/poindexter#394.
if [ -f release-please-config.poindexter.json ]; then
  cp release-please-config.poindexter.json release-please-config.json
  git add release-please-config.json
fi
git rm --cached --quiet release-please-config.poindexter.json 2>/dev/null || true   # the alt config itself stays internal

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
git rm --cached --quiet docs/architecture/module-v1-phase-1-plan-2026-05-13.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/module-v1-phase-2-plan-2026-05-13.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/declarative-data-plane-rfc-2026-04-24.md 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/writing_samples/ 2>/dev/null || true  # private writing style training data
git rm -r --cached --quiet mcp-server-gladlabs/ 2>/dev/null || true          # private operator MCP server

# === Module v1 private business modules (Glad-Labs/poindexter#490) ===
# Modules declared with visibility="private" in their ModuleManifest live
# only in the glad-labs-stack operator overlay. Until Phase 5 ships a
# manifest-aware filter, list each private module's surface explicitly.
# - FinanceModule: Mercury banking integration (read-only). Contains no
#   actual credentials (those live encrypted in app_settings.value) but
#   the module structure, CLI subcommand, and DB schema are all
#   operator-scoped and shouldn't ship to public OSS.
git rm -r --cached --quiet src/cofounder_agent/modules/finance/ 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/poindexter/cli/finance.py 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/tests/unit/modules/finance/ 2>/dev/null || true
git rm --cached --quiet docs/operations/finance-module-operator.md 2>/dev/null || true   # private operator-overlay setup doc (Mercury banking)

# Patch the substrate registration + CLI wiring so the stripped tree is
# internally consistent. Each pattern is a literal string; grep -v -F
# drops the line(s) that contain it. The patches are idempotent (re-
# running on an already-patched tree is a no-op).
PRIVATE_MODULE_REGISTRY_PATTERNS=(
  '"modules.finance"'
  '"modules.finance.jobs.poll_mercury"'
  '# FinanceModule F1'
  '# integration. visibility=private'
  '# FinanceModule F2 polling job'
  '# from Mercury hourly. Gated by mercury_enabled in app_settings.'
)
PRIVATE_MODULE_CLI_PATTERNS=(
  'from .finance import finance_group'
  'main.add_command(finance_group, name="finance")'
)
for pat in "${PRIVATE_MODULE_REGISTRY_PATTERNS[@]}"; do
  if [ -f src/cofounder_agent/plugins/registry.py ]; then
    grep -v -F "$pat" src/cofounder_agent/plugins/registry.py \
      > src/cofounder_agent/plugins/registry.py.tmp \
      && mv src/cofounder_agent/plugins/registry.py.tmp \
            src/cofounder_agent/plugins/registry.py
    git add src/cofounder_agent/plugins/registry.py
  fi
done
for pat in "${PRIVATE_MODULE_CLI_PATTERNS[@]}"; do
  if [ -f src/cofounder_agent/poindexter/cli/app.py ]; then
    grep -v -F "$pat" src/cofounder_agent/poindexter/cli/app.py \
      > src/cofounder_agent/poindexter/cli/app.py.tmp \
      && mv src/cofounder_agent/poindexter/cli/app.py.tmp \
            src/cofounder_agent/poindexter/cli/app.py
    git add src/cofounder_agent/poindexter/cli/app.py
  fi
done

# === Private infrastructure (Matt's local setup, not useful publicly) ===
git rm --cached --quiet .woodpecker.yml 2>/dev/null || true                  # legacy Gitea CI config (unused post-decommission)
git rm -r --cached --quiet .gitea/ 2>/dev/null || true                       # gitea decommissioned; entire workflow folder is dead
git rm --cached --quiet scripts/migrate-poindexter-rename.sh 2>/dev/null || true  # one-shot rebrand script, specific to Matt's install
git rm --cached --quiet scripts/sync-to-github.sh 2>/dev/null || true        # the sync script itself — internal-only, exposes strip logic
git rm --cached --quiet scripts/push-everywhere.sh 2>/dev/null || true       # local-dev-only, two-remote push helper
git rm --cached --quiet scripts/install-git-hooks.sh 2>/dev/null || true     # local-dev-only, sets up the pushe alias
git rm -r --cached --quiet .githooks/ 2>/dev/null || true                    # the hooks themselves only fire after install-git-hooks.sh runs — paired stripping
git rm --cached --quiet scripts/system-health-check.sh 2>/dev/null || true   # operator-specific health probes (Matt's install)
git rm --cached --quiet scripts/claude-sessions.ps1 2>/dev/null || true       # Windows scheduled-task setup for autonomous Claude sessions (Matt's install)
git rm --cached --quiet scripts/run-claude-session.cmd 2>/dev/null || true    # cmd wrapper for above
git rm --cached --quiet scripts/sync-premium-prompts.py 2>/dev/null || true   # syncs premium prompts from Glad Labs operator stash, no-op for public users
git rm --cached --quiet scripts/kuma_bootstrap.py 2>/dev/null || true         # operator-specific Kuma monitor list (gladlabs.io / Tailscale Funnel), not a reusable template

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
git rm --cached --quiet .github/workflows/release-please.yml 2>/dev/null || true  # release-please runs on glad-labs-stack only; the workflow file's repo gate is fine but it leaks the internal repo name and is dead code on the mirror
git rm --cached --quiet .github/workflows/public-mirror-safety.yml 2>/dev/null || true  # operator-overlay-specific leak patterns; the underlying script (scripts/ci/check_public_mirror_safety.py) still ships as a generic skeleton OSS users adapt to their own strip list
git rm --cached --quiet scripts/settings_defaults_extract.json 2>/dev/null || true  # AST-extracted seed data containing operator install paths (regenerated by scripts/extract_settings_defaults.py from operator codebase; not useful to public consumers)
git rm --cached --quiet scripts/settings_secret_keys.json 2>/dev/null || true       # paired secret-key blocklist used by extract.json regen

# === Operator-specific files (Glad Labs internal, not customer-facing) ===
git rm --cached --quiet docker-compose.local.yml 2>/dev/null || true          # Matt's full local stack with pgAdmin, SDXL, etc.

# === Operator skill toggles (private to Glad Labs install) ===
# Renamed skills/openclaw/ → skills/poindexter/ on 2026-05-05; keep the
# legacy path strip for one rotation cycle in case any in-flight branch
# still has the old layout.
git rm --cached --quiet skills/poindexter/gladlabs-config.json 2>/dev/null || true
git rm --cached --quiet skills/openclaw/gladlabs-config.json 2>/dev/null || true
git rm --cached --quiet .env.example 2>/dev/null || true                      # Legacy; customers use poindexter setup

# === Premium Grafana dashboards (Seed Package — keep only pipeline-merged free) ===
git rm --cached --quiet infrastructure/grafana/dashboards/approval-queue.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/cost-analytics.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/infrastructure-data.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/link-registry.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/quality-content.json 2>/dev/null || true

# === Gitleaks baseline — SHIPPED to public mirror, NOT stripped ===
# 2026-05-12: previously this line stripped .gitleaks-baseline.json from
# the public tree, which meant the public mirror's security workflow
# (.github/workflows/security.yml) had no baseline and re-discovered the
# 53 historical-false-positive findings on every push — causing the
# entire gate to fail loudly on the public side.
#
# The baseline contains: commit SHAs + file paths + the matched strings
# (rotated credentials, fixture-shaped strings, doc curl examples) +
# author email. All of these are ALREADY public via git history on the
# mirror — the file adds no new information. Shipping it gives both
# repos a consistent baseline so the CI gate works on both sides.

# === Cosmetic substitutions — rewrite internal-repo URLs in remaining tracked files ===
#
# A long tail of code/CHANGELOG comments references the source repo by name
# (`Glad-Labs/glad-labs-stack`). The source name is functionally accurate
# (release-please writes commit links from glad-labs-stack) but reveals the
# internal repo and produces broken issue/PR links on the public mirror. A
# Python pass rewrites the org/repo to the public name; specific
# `glad-labs-stack#N` issue references degrade to `poindexter#N` (numbers
# don't always match — better a broken link to the right repo than a
# working link to the private one).
python3 - <<'PYSUB'
import pathlib, re, subprocess
tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
changed = 0
for rel in tracked:
    p = pathlib.Path(rel)
    if not p.is_file():
        continue
    # Skip binary types — only rewrite text we know we wrote.
    # Allowlist: known text extensions PLUS Dockerfile* and dotfiles
    # (.gitignore, .githooks/*) which don't have a regular suffix.
    text_exts = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh", ".sql", ".txt", ".cfg", ".ini"}
    name = p.name
    is_dockerfile = name.startswith("Dockerfile")
    is_dotfile = name.startswith(".") and "." not in name[1:]  # .gitignore etc.
    if p.suffix.lower() not in text_exts and not is_dockerfile and not is_dotfile:
        continue
    try:
        txt = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    new = txt.replace("Glad-Labs/glad-labs-stack", "Glad-Labs/poindexter")
    if new != txt:
        p.write_text(new, encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", rel], check=False)
        changed += 1
print(f"[sync] cosmetic substitution: rewrote glad-labs-stack -> poindexter in {changed} files")
PYSUB

# Commit the removal + substitutions (temporary — only pushed to github, never to origin/glad-labs-stack)
git commit -m "sync: exclude private files for public repo" --allow-empty 2>/dev/null

# === Leak guard — abort sync if forbidden patterns made it through ===
#
# Even after all the path strips above, content-level leaks can slip in
# (e.g. a new release-please CHANGELOG entry mentioning a private module,
# or a hand-written doc adding the operator's Tailnet IP). This guard
# greps the staged tree for known operator-private patterns and aborts
# the push if it finds any — better to fail loudly than to ship the leak.
#
# Add new patterns when a new private module / personal value is found.
# See 2026-05-14 audit which seeded this list: bank balance, hardware
# cost, Tailnet IP, Tailscale Funnel, operator-overlay module names.
echo "[sync] Running leak guard..."
LEAK_PATTERNS=(
  '100\.81\.93\.12'                # Tailnet IP
  'taild4f626\.ts\.net'            # Tailscale Funnel hostname
  '\bnightrider\b'                  # Tailscale Funnel hostname fragment (also a test-fixture leak)
  '7877\.14'                        # Hardware cost total
  '362\.75'                         # Mercury balance literal
  'mercury_'                        # any mercury_* key name (catches the whole family)
  'C:[\\/]Users[\\/]mattm'          # operator's Windows home path
  '/c/Users/mattm'                  # bash-style operator home path
  'mattg-stack'                     # operator's GitHub username
  'Glad-Labs/glad-labs-stack'       # internal repo name (cosmetic sub above should fix all)
)
# Files that legitimately contain the patterns above because they DEFINE
# what to strip (sync filter, regen-script blocklists). The leak guard
# would self-report on these without this exclude list.
LEAK_GUARD_ALLOW=(
  'scripts/regen-app-settings-doc.py'         # the redaction blocklist itself
  'scripts/ci/check_public_mirror_safety.py'  # parallel pre-merge lint with the same pattern list
)
LEAK_FOUND=0
for pat in "${LEAK_PATTERNS[@]}"; do
  # `git ls-files | xargs grep` reads only what's tracked in the temp
  # branch (the stripped tree), so a pattern in a private-only file
  # already removed from index won't trip the guard.
  hits=$(git ls-files | xargs grep -l -E "$pat" 2>/dev/null || true)
  # Drop allowlisted self-referential files from the hit list.
  for allow in "${LEAK_GUARD_ALLOW[@]}"; do
    hits=$(echo "$hits" | grep -v -F "$allow" || true)
  done
  if [[ -n "$hits" ]]; then
    echo "[sync] LEAK DETECTED — pattern: $pat"
    echo "$hits" | sed 's/^/    /'
    LEAK_FOUND=1
  fi
done
if [[ "$LEAK_FOUND" -ne 0 ]]; then
  echo "[sync] Aborting push — fix leaks in source or add to strip filter."
  git checkout -f "$BRANCH"
  git branch -D "$TEMP_BRANCH"
  exit 1
fi
echo "[sync] Leak guard passed."

# Force-push this clean branch to GitHub as main, but with a LEASE.
#
# Why force-push: this is the documented "intentional posture" for the public
# mirror — see CLAUDE.md § Mirror force-push posture. The mirror is rebuilt
# from scratch on every sync (filter → push), so a fast-forward-only push
# would just keep the mirror permanently stale.
#
# Why --force-with-lease (NOT plain --force): if mirror/main has moved since
# we captured EXPECTED_MIRROR_SHA at the top of this script, something
# unexpected pushed to the mirror (a real human PR? a hand-edit?) and we
# want to ABORT loudly instead of silently overwriting it. Plain --force
# would just steamroll. See #392 + the e302fa5d incident, which silently
# reverted production code from PRs #157 and #161 (#384 + #391) for ~5 days
# of dark image / podcast / video media_assets recording.
#
# Edge case: if EXPECTED_MIRROR_SHA is empty (first-ever push, branch doesn't
# exist on the mirror yet), `--force-with-lease=ref:` (empty SHA) tells git
# "expect this ref to NOT exist" and is the right safety check for the
# brand-new-branch case.
git push "$GITHUB_REMOTE" "${TEMP_BRANCH}:${BRANCH}" \
  --force-with-lease="${BRANCH}:${EXPECTED_MIRROR_SHA}" 2>&1

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
