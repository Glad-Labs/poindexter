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
git rm --cached --quiet docs/architecture/module-v1-phase-1-plan-2026-05-13.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/module-v1-phase-2-plan-2026-05-13.md 2>/dev/null || true
git rm --cached --quiet docs/architecture/declarative-data-plane-rfc-2026-04-24.md 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/writing_samples/ 2>/dev/null || true  # private writing style training data
git rm -r --cached --quiet mcp-server-gladlabs/ 2>/dev/null || true          # private operator MCP server

# === Module v1 private business modules (Glad-Labs/poindexter#490) ===
# Modules declared visibility="private" live only in the glad-labs-stack
# operator overlay. As of Module v1 Phase 5 (2026-06-04) a private module is
# stripped by DELETING ITS PACKAGE DIRECTORY ALONE: in-tree module + job +
# CLI discovery is presence-based (plugins/registry.py directory scan +
# modules/<name>/jobs/JOBS + Module.register_cli), and the module is NOT
# listed in pyproject.toml. So there is nothing to patch in the substrate —
# no registry.py / cli/app.py line-surgery, no pyproject entry to strip, and
# the module's CLI now lives at modules/finance/cli.py so it rides the
# directory. See docs/architecture/2026-06-04-module-visibility-sync-design.md.
# - FinanceModule: Mercury banking (read-only). No credentials live in the
#   tree (they're encrypted in app_settings.value), but the module structure,
#   CLI, and DB schema are operator-scoped and don't ship to public OSS.
git rm -r --cached --quiet src/cofounder_agent/modules/finance/ 2>/dev/null || true
git rm -r --cached --quiet src/cofounder_agent/tests/unit/modules/finance/ 2>/dev/null || true
git rm --cached --quiet docs/operations/finance-module-operator.md 2>/dev/null || true   # private operator-overlay setup doc (Mercury banking)

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
git rm --cached --quiet scripts/glitchtip_audit.py 2>/dev/null || true        # one-shot admin-credential script (operator-only; was accidentally tracked with a hardcoded password default)

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
# Dependabot config must NOT ship to the public mirror. Per
# ``feedback_check_issue_routing_first``: automation always writes to
# glad-labs-stack. When this file lived in the mirror, dependabot ran
# in BOTH repos and spawned duplicate PRs in poindexter (#510-#516
# 2026-05-18..19 were all noise — Matt flagged 2026-05-22). The
# private repo is the dependabot-of-record; the public mirror should
# inherit dep bumps through the normal commit→sync flow.
git rm --cached --quiet .github/dependabot.yml 2>/dev/null || true
git rm --cached --quiet .github/workflows/ci.yml 2>/dev/null || true          # Deploy runs from glad-labs-stack, not poindexter
git rm --cached --quiet .github/workflows/sync-to-public-poindexter.yml 2>/dev/null || true  # The mirror sync ITSELF lives only on glad-labs-stack — shipping it to the public mirror caused recursive runs that fail every time (no POINDEXTER_DEPLOY_KEY secret on the public side) and burn CI minutes
git rm --cached --quiet .github/workflows/release-please.yml 2>/dev/null || true  # release-please runs on glad-labs-stack only; the workflow file's repo gate is fine but it leaks the internal repo name and is dead code on the mirror
git rm --cached --quiet .github/workflows/release-mirror-to-public.yml 2>/dev/null || true  # the workflow that creates matching tags+Releases on Glad-Labs/poindexter after release-please cuts one on stack — only runs on the source repo, dead code (and leaks internal name) on the mirror itself
git rm --cached --quiet .github/workflows/public-mirror-safety.yml 2>/dev/null || true  # operator-overlay-specific leak patterns; the underlying script (scripts/ci/check_public_mirror_safety.py) is stripped too — see the mirror-tooling block below
git rm --cached --quiet .github/workflows/regen-app-settings-doc.yml 2>/dev/null || true  # nightly auto-PR for docs/reference/app-settings.md — must only run on glad-labs-stack (source of truth). On the public mirror it would open PRs against poindexter, which get force-pushed-over on the next sync, leaving disconnected branches behind. The regenerated doc still ships to the mirror through the normal commit→sync flow once the stack-side PR merges.
git rm --cached --quiet .github/workflows/sync-claude-md.yml 2>/dev/null || true  # sibling nightly auto-PR (CLAUDE.md is itself stripped, so this workflow only makes sense on glad-labs-stack). It now mints the RELEASE_PLEASE_APP_* token, and those secrets do not exist on the public mirror, so shipping it would turn poindexter's nightly run red. Stripped alongside regen-app-settings-doc.yml.
git rm --cached --quiet .github/workflows/playwright-e2e.yml 2>/dev/null || true  # frontend E2E for web/public-site, which is stripped from the mirror — on poindexter the build step fails 10/10 with "npm error No workspaces found: --workspace=web/public-site". Non-required check; it tests a frontend that doesn't exist on the mirror (the backend-dependent specs are dispatch-only and equally moot here).
git rm --cached --quiet scripts/settings_defaults_extract.json 2>/dev/null || true  # AST-extracted seed data containing operator install paths (regenerated by scripts/extract_settings_defaults.py from operator codebase; not useful to public consumers)
git rm --cached --quiet scripts/settings_secret_keys.json 2>/dev/null || true       # paired secret-key blocklist used by extract.json regen

# === Operator mirror-sync / doc-gen tooling (Glad-Labs/poindexter#1287) ===
# The leak guard + the app-settings doc generator carry operator-private
# values INLINE as regex/string literals (the blocklist of things they
# redact: Tailnet IP, Tailscale Funnel host, bank-balance / hardware-cost
# figures, the operator's GitHub handle + name). They were SHIPPING to the
# public mirror — the guard whose job is to prevent operator-PII leaks was
# itself the leak (the `_LEAK_GUARD_ALLOW` self-exemption masked it). These
# scripts only ever run on glad-labs-stack: the guard runs in CI
# (public-mirror-safety.yml, stripped above) + at sync time below, and the
# doc generator runs nightly (regen-app-settings-doc.yml, stripped above) —
# the *output* (docs/reference/app-settings.md) still ships through the
# normal commit→sync flow. The mirror runs neither workflow, so the scripts
# are dead code there; stripping them is zero-loss and closes the leak.
# `git rm --cached` is index-only — the files stay on disk so the leak-guard
# invocation later in THIS script still runs from the working tree.
git rm --cached --quiet scripts/ci/check_public_mirror_safety.py 2>/dev/null || true   # leak guard — inline operator literals in _LEAK_PATTERNS + _SUBSTRATE_LINE_STRIPS
git rm --cached --quiet scripts/regen-app-settings-doc.py 2>/dev/null || true          # app-settings doc generator — inline operator literals in _PRIVATE_VALUE_PATTERNS / _PRIVATE_KEY_PATTERNS
# Their unit tests load/inspect the two stripped scripts (and the stripped
# sync-to-github.sh), so they'd ImportError / FileNotFound on the mirror's
# unit-tests run. Strip the whole cluster together — it tests operator-only
# mirror tooling the mirror never executes.
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_gitea.py 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_multiline.py 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_name_regex.py 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_strip_list.py 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_regen_app_settings_doc.py 2>/dev/null || true
git rm --cached --quiet src/cofounder_agent/tests/unit/scripts/test_sync_script_leak_guard_delegation.py 2>/dev/null || true

# === Operator-specific files (Glad Labs internal, not customer-facing) ===
git rm --cached --quiet docker-compose.local.yml 2>/dev/null || true          # Matt's full local stack with pgAdmin, SDXL, etc.

# === Operator skill toggles (private to Glad Labs install) ===
# Renamed skills/openclaw/ → skills/poindexter/ on 2026-05-05; keep the
# legacy path strip for one rotation cycle in case any in-flight branch
# still has the old layout.
git rm --cached --quiet skills/poindexter/gladlabs-config.json 2>/dev/null || true
git rm --cached --quiet skills/openclaw/gladlabs-config.json 2>/dev/null || true
# .env.example SHIPS to the public mirror (poindexter#607) — it documents
# every ${VAR} the OSS single-container docker-compose.yml consumes, and
# that compose file's quickstart instructs `cp .env.example .env`. Stripping
# it left public users with a compose file that referenced a template that
# didn't exist. (`poindexter setup` remains the path for the full operator
# stack, but the bare quickstart needs this template.)
git rm --cached --quiet scripts/bootstrap.sh 2>/dev/null || true              # References stripped files (docker-compose.local.yml) and dead Woodpecker CI; poindexter setup --auto covers fresh-install flow

# === Premium Grafana dashboards (Seed Package — keep only pipeline-merged free) ===
git rm --cached --quiet infrastructure/grafana/dashboards/approval-queue.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/cost-analytics.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/infrastructure-data.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/link-registry.json 2>/dev/null || true
git rm --cached --quiet infrastructure/grafana/dashboards/mission-control.json 2>/dev/null || true   # embeds operator-only Tailscale Funnel voice URL + Pyroscope/Loki/Tempo links
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
# The previous version of this block needed a ``COSMETIC_SUB_SKIP_FILES``
# escape hatch because several files held ``Glad-Labs/glad-labs-stack``
# as a literal code/data value (voice_agent_livekit's repo tuple,
# fixture keys in three test files). The 2026-05-22 cleanup pass
# eliminated those literals — voice_agent_livekit reads repos from
# ``app_settings.voice_agent_pr_repos`` now, and the test fixtures
# moved to generic ``Test-Org/test-repo`` placeholders. The skip-list
# is no longer needed; the substitution is back to pure cosmetic
# behavior.

python3 - <<'PYSUB'
import pathlib, subprocess, sys

# "Is this a rewritable text file?" is delegated to the leak guard's
# canonical ``_is_text_file`` predicate (scripts/ci/check_public_mirror_safety.py)
# rather than a second inline extension list. The guard SCANS a file set;
# the rewrite must NORMALIZE the same set — keeping two hand-maintained
# lists is exactly the drift class that already took the sync down once
# (the LEAK_PATTERNS/allowlist consolidation, 2026-05-27). It drifted
# again on the extension axis: ``.ps1`` was in the guard's _TEXT_EXTS but
# NOT this rewrite's old inline allowlist, so voice-brain-host.ps1's
# internal-repo doc-comment link sailed past the rewrite and tripped the
# post-rewrite belt-and-suspenders check below. One predicate, no drift.
sys.path.insert(0, "scripts/ci")
from check_public_mirror_safety import _is_text_file

OLD = b"Glad-Labs/glad-labs-stack"
NEW = b"Glad-Labs/poindexter"

tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
changed = 0
for rel in tracked:
    p = pathlib.Path(rel)
    if not p.is_file():
        continue
    if not _is_text_file(rel):
        continue
    # Byte-level substitution so line endings are preserved verbatim. The
    # previous text-mode write forced newline="\n", which would silently
    # convert CRLF files (.ps1/.cmd/.bat are eol=crlf per .gitattributes)
    # to LF on rewrite — the real reason .ps1 had been excluded from the
    # old inline allowlist. The literal never spans a line boundary, so a
    # raw bytes .replace() is safe and ending-agnostic.
    raw = p.read_bytes()
    new = raw.replace(OLD, NEW)
    if new != raw:
        p.write_bytes(new)
        subprocess.run(["git", "add", rel], check=False)
        changed += 1
print(f"[sync] cosmetic substitution: rewrote glad-labs-stack -> poindexter in {changed} files")
PYSUB

# === CHANGELOG.md private-key redaction ============================
# release-please's auto-generated CHANGELOG entries pick up commit
# messages mentioning private app_settings keys (mercury_api_token,
# operator hardware values, Tailnet IPs etc). The leak guard below
# catches these, but only by aborting the push — that means every
# release-please merge wedges the mirror until someone hand-edits.
# Strip offending lines from CHANGELOG.md instead so the sync stays
# unattended. Single line-redact pass — preserves all OTHER changelog
# entries so the public CHANGELOG stays useful.
if [[ -f CHANGELOG.md ]]; then
  python3 -c "
import pathlib, re
p = pathlib.Path('CHANGELOG.md')
text = p.read_text(encoding='utf-8')
# Patterns that warrant redaction at LINE granularity (whole bullet
# point dropped). Must match the strictest LEAK_PATTERNS below so the
# guard doesn't re-fire after this filter runs.
LINE_REDACT_RE = re.compile(r'(?:mercury_|nightrider|taild4f626|7877\.14|362\.75|mattg-stack)', re.IGNORECASE)
out = []
dropped = 0
for line in text.splitlines(keepends=True):
    if LINE_REDACT_RE.search(line):
        dropped += 1
        continue
    out.append(line)
if dropped:
    p.write_text(''.join(out), encoding='utf-8', newline='\n')
    print(f'[sync] CHANGELOG.md: redacted {dropped} private-key line(s)')
"
  git add CHANGELOG.md 2>/dev/null || true
fi

# === docs.json: rewrite operator-branded URLs to poindexter-neutral equivalents ===
# docs.json is the Mintlify config that ships with the public mirror (the public docs at
# gladlabs.mintlify.app are operator-hosted and DO reference gladlabs.io intentionally on
# the private side, but the public OSS tree must not embed an operator's domain — a fork
# would inherit the wrong branding). Rewrite the two operator URLs in place so poindexter
# forks get neutral placeholders they can swap out via `poindexter setup`.
# Scope: only docs.json — the broader gladlabs.io leak-guard pattern only catches SQL
# VALUES tuples; this targeted rewrite handles the JSON "href"/"website" shapes.
if [ -f docs.json ]; then
  python3 -c "
import pathlib, json
p = pathlib.Path('docs.json')
txt = p.read_text(encoding='utf-8')
# Rewrite the operator-branded gladlabs.io URLs to poindexter-neutral placeholders.
# The cosmetic substitution above already rewrote glad-labs-stack -> poindexter;
# these replacements fix the remaining domain-specific links.
txt = txt.replace('https://gladlabs.io/product', 'https://github.com/Glad-Labs/poindexter')
txt = txt.replace('https://www.gladlabs.io', 'https://github.com/Glad-Labs/poindexter')
p.write_text(txt, encoding='utf-8', newline='\n')
print('[sync] docs.json: rewrote gladlabs.io URLs to poindexter-neutral equivalents')
"
  git add docs.json 2>/dev/null || true
fi

# Commit the removal + substitutions (temporary — only pushed to github, never to origin/glad-labs-stack)
git commit -m "sync: exclude private files for public repo" --allow-empty 2>/dev/null

# === Leak guard — abort sync if forbidden patterns made it through ===
#
# Even after all the path strips above, content-level leaks can slip in
# (e.g. a new release-please CHANGELOG entry mentioning a private module,
# or a hand-written doc adding the operator's Tailnet IP).
#
# SINGLE SOURCE OF TRUTH: the pattern list + allowlist live in
# ``scripts/ci/check_public_mirror_safety.py`` (which is also the
# pre-merge CI gate via .github/workflows/public-mirror-safety.yml).
# This step just invokes that script against the post-filter temp
# branch — same patterns, same allowlist, one place to update.
#
# Pre-2026-05-27 this script duplicated the pattern + allowlist arrays
# inline. The duplication caused 5+ consecutive sync failures when
# PR #619 added a test-fixture file that was allowlisted on the CI
# side but not here. Consolidating both lists into one Python module
# removed that drift class entirely.
echo "[sync] Running leak guard (delegating to scripts/ci/check_public_mirror_safety.py)..."
if ! python3 scripts/ci/check_public_mirror_safety.py; then
  echo "[sync] Aborting push — fix leaks in source or add to strip filter."
  git checkout -f "$BRANCH"
  git branch -D "$TEMP_BRANCH"
  exit 1
fi

# Belt-and-suspenders post-rewrite check: ``Glad-Labs/glad-labs-stack``
# is intentionally NOT in the Python guard's pattern list because the
# Python guard runs on the pre-rewrite source tree where release-please
# CHANGELOG entries legitimately contain that string. By the time we
# reach this point in sync-to-github.sh, the cosmetic sed pass earlier
# (the ``Glad-Labs/glad-labs-stack`` → ``Glad-Labs/poindexter`` rewrite)
# has already run on the temp branch, so any surviving occurrence is
# a real leak — e.g. binary file the sed missed, an alternate-case
# variant the rewrite didn't normalize, a file the rewrite passed over.
INTERNAL_REPO_HITS=$(git ls-files | xargs grep -l -E 'Glad-Labs/glad-labs-stack' 2>/dev/null || true)
if [[ -n "$INTERNAL_REPO_HITS" ]]; then
  echo "[sync] LEAK DETECTED — post-rewrite check found internal repo references that survived the sed rewrite:"
  echo "$INTERNAL_REPO_HITS" | sed 's/^/    /'
  echo "[sync] Aborting push — fix leaks in source or extend the sync rewrite pass."
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
