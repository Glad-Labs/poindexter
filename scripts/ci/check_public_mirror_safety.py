#!/usr/bin/env python3
"""Lint the source tree for content that would leak to the public mirror.

**Single source of truth for leak-guard patterns + allowlist.** This
module owns the canonical ``_LEAK_PATTERNS`` and ``_LEAK_GUARD_ALLOW``
arrays. Both enforcement layers consume them:

- **Pre-merge (CI):** ``.github/workflows/public-mirror-safety.yml``
  runs this script on every PR + push to ``main``. Fail-loud blocks
  the PR so the fix lands with the offending change.
- **Sync-time:** ``scripts/sync-to-github.sh`` invokes this script
  against the post-filter temp branch right before force-pushing to
  ``Glad-Labs/poindexter``. Belt-and-suspenders for anything that
  bypassed CI (direct push to main, hand-edit on the mirror, etc.).

Pre-2026-05-27 the bash sync script duplicated the pattern + allowlist
arrays inline. The duplication caused 5+ consecutive sync failures when
a test-fixture file was allowlisted on the CI side but not on the
sync-script side. Consolidating into this module removed that drift
class entirely.

How it works
============

1. **Classify every tracked file** as either ``WOULD_SHIP`` (sync filter
   keeps it) or ``WOULD_STRIP`` (sync filter removes it). The classifier
   mirrors the path patterns in ``sync-to-github.sh``. When the script
   runs from the post-filter temp branch, ``git ls-files`` already
   returns the stripped tree — both layers converge on the same
   file set.

2. **Run LEAK_PATTERNS against WOULD_SHIP files only.** A pattern hit in
   a WOULD_STRIP file is fine (that file never ships) — only flag the
   ones the public will actually see.

3. **Allowlist self-referential files** (the redaction-blocklist script
   names what it filters, so it'd self-report otherwise).

4. **Report + exit nonzero on any violation**, with file + line + the
   pattern that matched so the PR author can fix in place.

Adding a new pattern
====================

When a new private value surfaces (new operator-overlay module, new
personal default, etc.):

1. Strip the value from source (or add to ``WOULD_STRIP_PATHS`` if it's
   a whole-file leak).
2. Add the pattern to ``_LEAK_PATTERNS`` below — one place, both
   layers pick it up automatically.
3. Run this script locally to verify (``python3
   scripts/ci/check_public_mirror_safety.py``).

The lone exception: ``Glad-Labs/glad-labs-stack`` lives inline in
``sync-to-github.sh`` instead of here. It's a post-rewrite belt-and-
suspenders check, scoped to the sync-time pass after the cosmetic sed
rewrite. Keeping it here would false-positive on every release-please
CHANGELOG entry in the source tree (those legitimately reference the
internal repo, the sync filter rewrites them to ``poindexter`` before
push).
"""

from __future__ import annotations

import fnmatch
import io
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Windows shells default stdout to cp1252, which crashes on the U+2192 etc.
# characters our leak-pattern descriptions or matched lines may contain.
# Force UTF-8 so the lint runs cleanly both in CI (Linux, UTF-8 already)
# and on the operator's PowerShell host.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:  # pragma: no cover — Python < 3.7 doesn't have reconfigure
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Classification — which files ship to public, which are stripped.
# Mirror the path patterns in ``scripts/sync-to-github.sh`` so this lint
# stays in sync with what the sync filter actually does.
# ---------------------------------------------------------------------------


# Directories whose entire contents are stripped from public. Use trailing
# ``/`` so we match the directory prefix only (a file at ``web/foo`` matches
# the ``web/public-site/`` prefix only if it's literally under that path).
_STRIP_DIR_PREFIXES = (
    "web/public-site/",
    "web/storefront/",
    "marketing/",
    "packages/",
    "docs/brand/",
    "docs/experiments/",
    "docs/superpowers/",
    "src/cofounder_agent/writing_samples/",
    "mcp-server-gladlabs/",
    "src/cofounder_agent/modules/finance/",
    "src/cofounder_agent/tests/unit/modules/finance/",
    ".gitea/",
    ".shared-context/",
    ".github/workflows-disabled/",
    ".githooks/",
)


# Specific files stripped from public. Wildcard with ``*`` is OK
# (matched via ``fnmatch``).
_STRIP_FILES = (
    "release-please-config.poindexter.json",
    "docs/operations/documentation-audit-2026-04-29.md",
    "docs/operations/migrations-audit-2026-04-27.md",
    "docs/operations/overnight-2026-04-27-summary.md",
    "docs/operations/public-site-audit-2026-04-27.md",
    "docs/operations/silent-failures-audit-2026-04-27.md",
    "docs/operations/test-coverage-2026-04-27.md",
    "docs/architecture/database-and-embeddings-plan-2026-04-24.md",
    "docs/architecture/module-v1-phase-1-plan-2026-05-13.md",
    "docs/architecture/module-v1-phase-2-plan-2026-05-13.md",
    "docs/architecture/declarative-data-plane-rfc-2026-04-24.md",
    "src/cofounder_agent/poindexter/cli/finance.py",
    "docs/operations/finance-module-operator.md",
    ".woodpecker.yml",
    "scripts/migrate-poindexter-rename.sh",
    "scripts/sync-to-github.sh",
    "scripts/push-everywhere.sh",
    "scripts/install-git-hooks.sh",
    "scripts/system-health-check.sh",
    "scripts/claude-sessions.ps1",
    "scripts/run-claude-session.cmd",
    "scripts/sync-premium-prompts.py",
    "scripts/kuma_bootstrap.py",
    "scripts/settings_defaults_extract.json",
    "scripts/settings_secret_keys.json",
    "src/cofounder_agent/services/taps/claude_code_sessions.py",
    "src/cofounder_agent/tests/unit/services/test_claude_code_sessions_tap.py",
    "CLAUDE.md",
    "src/cofounder_agent/.coverage",
    ".github/COMMIT_MESSAGE_*.txt",
    ".github/create-tech-debt-issues.sh",
    ".github/tech-debt-issues.json",
    ".github/workflows/ci.yml",
    ".github/workflows/sync-to-public-poindexter.yml",
    ".github/workflows/release-please.yml",
    ".github/workflows/public-mirror-safety.yml",
    "docker-compose.local.yml",
    "skills/poindexter/gladlabs-config.json",
    "skills/openclaw/gladlabs-config.json",
    ".env.example",
    # bootstrap.sh references stripped files (.env.example, docker-compose.local.yml)
    # and the dead Woodpecker CI (WOODPECKER_SECRET=...). poindexter setup --auto
    # covers the fresh-install flow. Stripped 2026-05-27 per security audit.
    "scripts/bootstrap.sh",
    "infrastructure/grafana/dashboards/approval-queue.json",
    "infrastructure/grafana/dashboards/cost-analytics.json",
    "infrastructure/grafana/dashboards/infrastructure-data.json",
    "infrastructure/grafana/dashboards/link-registry.json",
    # mission-control.json embeds the operator's Tailscale Funnel voice URL +
    # other operator-specific dashboard links (Pyroscope, Loki, Tempo). It's
    # the operator's top-level view and not part of the public Poindexter
    # product surface.
    "infrastructure/grafana/dashboards/mission-control.json",
    "infrastructure/grafana/dashboards/quality-content.json",
)


# Files that are public-bound BUT legitimately contain leak-shaped strings
# because they DEFINE the redaction blocklist. Without this allowlist the
# lint would self-report on its own pattern list.
_LEAK_GUARD_ALLOW = (
    "scripts/regen-app-settings-doc.py",
    # This script itself — pattern strings appear in source.
    "scripts/ci/check_public_mirror_safety.py",
    # Sync filter — same reason; also stripped from public sync so the
    # public mirror doesn't see it, but CI runs against the source tree
    # where it's present.
    "scripts/sync-to-github.sh",
    # Contract test for the gitea# LEAK_GUARD pattern — its fixtures
    # have to literally contain "gitea#NNN" shapes for the assertions
    # to verify the regex matches them. Without this allow-entry the
    # guard self-reports on its own test.
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_gitea.py",
    # Contract test for the operator-name LEAK_GUARD regex — must contain
    # the operator name in synthetic test strings and docstrings so the
    # assertions can verify the pattern fires on them. Same self-referential
    # exemption as the gitea test above.
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_name_regex.py",
    # Contract test for the multi-line VALUES tuple + bare-gladlabs.io regex
    # added in #619 — fixtures contain literal leak shapes (gladlabs.io
    # seeded URLs, multi-line mercury_ VALUES tuples) so the assertions
    # can verify the patterns fire on them. Same self-referential
    # exemption as the two test files above. Closes the post-#619 main
    # breakage where this file was added but not allowlisted. Since
    # 2026-05-27 sync-to-github.sh delegates to this module, so this
    # entry is the only place the allowlist needs to live.
    "src/cofounder_agent/tests/unit/scripts/test_check_public_mirror_safety_multiline.py",
)


# Line-level patches the sync filter applies to specific source files
# (via ``grep -v -F`` in ``sync-to-github.sh``). Lines containing any of
# these substrings get dropped before the file ships, so the lint should
# ignore matches on them too — they never reach public.
#
# Keys: the file the patch is applied to.
# Values: list of literal substrings; a matching line is filtered.
#
# Mirror ``PRIVATE_MODULE_REGISTRY_PATTERNS`` + ``PRIVATE_MODULE_CLI_PATTERNS``
# from ``sync-to-github.sh``. When a new private module ships, update both.
_SUBSTRATE_LINE_STRIPS: dict[str, tuple[str, ...]] = {
    "src/cofounder_agent/plugins/registry.py": (
        '"modules.finance"',
        '"modules.finance.jobs.poll_mercury"',
        "# FinanceModule F1",
        "# integration. visibility=private",
        "# FinanceModule F2 polling job",
        "# from Mercury hourly. Gated by mercury_enabled in app_settings.",
    ),
    "src/cofounder_agent/poindexter/cli/app.py": (
        "from .finance import finance_group",
        'main.add_command(finance_group, name="finance")',
    ),
    # docs.json: the sync filter rewrites the two operator-branded gladlabs.io
    # URLs to poindexter-neutral equivalents before pushing (see docs.json
    # rewrite block in sync-to-github.sh, 2026-05-27 audit). The CI lint runs
    # against the pre-rewrite source tree and would false-positive on those
    # lines if we don't tell it they'll be substituted at sync time.
    # The gladlabs.io leak-guard pattern uses a SQL VALUES shape, so it doesn't
    # currently flag docs.json's JSON href/website strings — this entry is here
    # as belt-and-suspenders documentation. Mirror sync-to-github.sh's rewrite.
    "docs.json": (
        '"href": "https://gladlabs.io/product"',
        '"website": "https://www.gladlabs.io"',
    ),
    # release-please's auto-generated CHANGELOG entries can pick up commit
    # messages that mention private app_settings keys, hardware values, or
    # Tailnet hostnames. The sync filter (sync-to-github.sh, the CHANGELOG
    # line-redact pass) drops these whole lines before pushing, so the
    # lint must skip them too — otherwise every release-please merge
    # wedges the mirror on a line that won't actually ship.
    # Keep in lock-step with the LINE_REDACT_RE in sync-to-github.sh.
    "CHANGELOG.md": (
        "mercury_",
        "nightrider",
        "taild4f626",
        "7877.14",
        "362.75",
        "mattg-stack",
    ),
}


def _line_would_be_stripped(rel_path: str, line: str) -> bool:
    """Return True if the sync filter drops this line from the public copy.

    Lets the lint skip lines that match the substrate-patch step in
    ``sync-to-github.sh``. Without this we'd false-positive on lines the
    public mirror never sees.
    """
    patches = _SUBSTRATE_LINE_STRIPS.get(rel_path)
    if not patches:
        return False
    return any(p in line for p in patches)


# ---------------------------------------------------------------------------
# Leak patterns — values / strings that must never appear in public files.
# Single source of truth: both the CI pre-merge check and the sync-time
# guard in ``sync-to-github.sh`` invoke this module's ``main()`` to scan
# this list.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakPattern:
    regex: re.Pattern[str]
    label: str
    why: str  # Short reason — surfaces in the error so the PR author knows
    multiline: bool = False
    """When True, the pattern is scanned against the WHOLE file body (one
    ``re.search`` call over the file text) instead of line-by-line. Use
    for shapes that legitimately span multiple lines — e.g. SQL ``VALUES``
    tuples in test fixtures where the keyword and the literal land on
    different lines. The scan reports the line containing the START of
    the match. Default False so existing patterns keep their line-by-line
    semantics. Closes cycle-4 audit #243."""


_LEAK_PATTERNS = (
    LeakPattern(
        re.compile(r"100\.81\.93\.12"),
        "operator Tailnet IP",
        "Replace with localhost or <your-tailnet-ip> placeholder.",
    ),
    LeakPattern(
        re.compile(r"taild4f626\.ts\.net"),
        "operator Tailscale Funnel hostname",
        "Replace with <your-funnel-host>.ts.net placeholder.",
    ),
    LeakPattern(
        re.compile(r"\bnightrider\b"),
        "Tailscale Funnel fragment / test-fixture host",
        "Use 'test-host' or a generic placeholder in fixtures.",
    ),
    LeakPattern(
        re.compile(r"7877\.14"),
        "operator hardware cost total",
        "Hardware cost is per-operator — use 0 in seeds.",
    ),
    LeakPattern(
        re.compile(r"362\.75"),
        "Mercury bank balance literal",
        "Never seed a real bank balance — the value belongs in app_settings only.",
    ),
    LeakPattern(
        re.compile(r"mercury_"),
        "operator-overlay (Mercury) key name family",
        "The FinanceModule is private — strip the key reference or "
        "add it to the doc generator's _PRIVATE_KEY_PATTERNS.",
    ),
    LeakPattern(
        re.compile(r"C:[\\/]Users[\\/]mattm"),
        "operator Windows home path",
        "Generalize the path — resolve from __file__ or a setting.",
    ),
    LeakPattern(
        re.compile(r"/c/Users/mattm"),
        "operator bash-style home path",
        "Generalize the path — resolve from $HOME or a setting.",
    ),
    LeakPattern(
        re.compile(r"mattg-stack"),
        "operator GitHub username",
        "Don't hardcode the operator's GitHub handle in OSS files.",
    ),
    # === Operator identity (feedback_no_operator_info_to_public_repo, 2026-05-23) ===
    LeakPattern(
        re.compile(r"matthew-gladding"),
        "operator LinkedIn URL fragment",
        "Don't seed a personal LinkedIn URL — leave the setting empty so "
        "the operator configures their own via `poindexter setup`.",
    ),
    LeakPattern(
        re.compile(r"[Mm]atthew (?:[A-Z]\.\s+)?[Gg]ladding"),
        "operator full name",
        "Don't hardcode the operator's full name in OSS files.",
    ),
    LeakPattern(
        re.compile(r"[Mm]att [Gg]ladding"),
        "operator informal name",
        "Don't hardcode the operator's name in OSS files.",
    ),
    LeakPattern(
        re.compile(r"[Mm]att''s (?:machine|production|host|specific)"),
        "operator-context phrasing in SQL seed comments",
        "Rewrite to generic 'the operator's <thing>' — no personal context "
        "in seeded descriptions.",
    ),
    LeakPattern(
        re.compile(r"phone Matt"),
        "operator-paging phrasing in comments",
        "Rewrite to 'operator-page' or 'operator-paging line'.",
    ),
    LeakPattern(
        # Citations to the decommissioned internal Gitea tracker. The
        # instance is gone (post-2026-04-30); these refs are dead links
        # that double as operator-history breadcrumbs the public mirror
        # shouldn't carry. Audit-flagged 2026-05-26 — 43 occurrences
        # slipped through because the existing '.gitea/' rule only
        # caught the directory, not citation form.
        re.compile(r"gitea#\d+"),
        "dead internal-tracker citation",
        "Rewrite to 'internal tracker' (preserves grammar) or remove "
        "the parenthetical entirely. Real GitHub issue refs use "
        "the '#NNN' or 'Glad-Labs/poindexter#NNN' form.",
    ),
    LeakPattern(
        # gladlabs.io as a DEFAULT seeded value. Catches a seed VALUES tuple
        # carrying a gladlabs.io string. Brand attribution mentions in
        # CLAUDE.md / README / public docs are OK (they don't match this
        # tuple shape) — only values inside an INSERT VALUES are flagged.
        # ``multiline=True`` + ``re.DOTALL`` so VALUES tuples that span
        # lines (pretty-printed SQL in test fixtures, baseline.seeds.sql
        # blocks) are caught too — closes cycle-4 audit #243 finding
        # where ``test_taps_db.py:224`` slipped through because ``VALUES``
        # and ``gladlabs.io`` landed on consecutive lines.
        re.compile(
            r"VALUES\s*\([^)]*?'[^']*?gladlabs\.io",
            re.DOTALL,
        ),
        "gladlabs.io as a seeded default value",
        "Replace with an empty string ('') in the seed. A fresh OSS "
        "install must NOT inherit Matt's site URL / email defaults — "
        "the operator sets these via `poindexter setup`.",
        multiline=True,
    ),
    # Note: ``Glad-Labs/glad-labs-stack`` is intentionally NOT a CI-time
    # leak pattern. The sync filter rewrites it to ``Glad-Labs/poindexter``
    # at push time across every text file (see the Python substitution
    # block in ``sync-to-github.sh``), so the public mirror never sees
    # the internal repo URL. The sync-time guard still flags it as a
    # belt-and-suspenders check; here we'd just trip on every
    # release-please CHANGELOG entry, which is noise.
)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _list_tracked_files(repo_root: Path) -> list[str]:
    """Run ``git ls-files`` so we lint exactly what would be pushed.

    Untracked files (build artifacts, ``.venv/``, ``__pycache__``) don't
    matter — only files git knows about ship anywhere.
    """
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=repo_root, text=True,
    )
    return [line for line in out.splitlines() if line.strip()]


def would_ship(rel_path: str) -> bool:
    """True if the sync filter ships this file to the public mirror.

    Mirrors ``sync-to-github.sh``'s strip list. A negative classification
    is conservative — when in doubt, treat as ``WOULD_SHIP`` so the leak
    guard examines it. (Better to false-positive on a stripped file than
    to false-negative on a leak.)
    """
    if rel_path in _LEAK_GUARD_ALLOW:
        return True  # Allowlisted self-referential files still ship; they
                     # just opt out of the LEAK_PATTERNS check below.
    for prefix in _STRIP_DIR_PREFIXES:
        if rel_path.startswith(prefix):
            return False
    for pattern in _STRIP_FILES:
        if "*" in pattern:
            if fnmatch.fnmatch(rel_path, pattern):
                return False
        elif rel_path == pattern:
            return False
    return True


# File extensions we actually scan for leak patterns. Binary types
# (images, fonts) can contain byte sequences that look like patterns by
# accident — and there's no operator-private content in a PNG anyway.
_TEXT_EXTS = frozenset({
    ".py", ".md", ".json", ".yml", ".yaml", ".toml", ".sh",
    ".ps1", ".sql", ".txt", ".cfg", ".ini", ".env",
})


def _is_text_file(rel_path: str) -> bool:
    """Restrict scanning to known text extensions + special cases.

    ``Dockerfile*`` and bare dotfiles (``.gitignore``) ship as text but
    have no regular suffix; handle them explicitly.
    """
    name = Path(rel_path).name
    if name.startswith("Dockerfile"):
        return True
    if name.startswith(".") and "." not in name[1:]:
        return True
    return Path(rel_path).suffix.lower() in _TEXT_EXTS


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    file: str
    line_no: int
    line_text: str
    pattern: LeakPattern


def scan(repo_root: Path) -> list[Hit]:
    hits: list[Hit] = []
    # Partition patterns once — every file uses the same split.
    line_patterns = tuple(p for p in _LEAK_PATTERNS if not p.multiline)
    multiline_patterns = tuple(p for p in _LEAK_PATTERNS if p.multiline)
    for rel in _list_tracked_files(repo_root):
        if not would_ship(rel):
            continue
        if rel in _LEAK_GUARD_ALLOW:
            continue
        if not _is_text_file(rel):
            continue
        full = repo_root / rel
        try:
            text = full.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Unreadable files (binary, encoding mismatch) don't carry
            # operator-private content in any case the lint cares about.
            continue
        # Pass 1 — line-by-line for line-scoped patterns (vast majority).
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _line_would_be_stripped(rel, line):
                continue
            for pat in line_patterns:
                if pat.regex.search(line):
                    hits.append(Hit(rel, line_no, line.rstrip(), pat))
        # Pass 2 — whole-file scan for multi-line patterns. Re-uses
        # ``_line_would_be_stripped`` against the line containing the
        # start of the match so substrate-line-strip exemptions still
        # apply. Reports the line containing the FIRST character of the
        # match for operator-readable error output.
        for pat in multiline_patterns:
            for match in pat.regex.finditer(text):
                start = match.start()
                line_no = text.count("\n", 0, start) + 1
                # Recover the line for both stripping check + the
                # error report.
                line_start = text.rfind("\n", 0, start) + 1
                line_end = text.find("\n", start)
                if line_end == -1:
                    line_end = len(text)
                line_text = text[line_start:line_end]
                if _line_would_be_stripped(rel, line_text):
                    continue
                hits.append(Hit(rel, line_no, line_text.rstrip(), pat))
    return hits


def _format_hit(hit: Hit) -> str:
    snippet = hit.line_text
    if len(snippet) > 100:
        snippet = snippet[:97] + "..."
    return (
        f"  {hit.file}:{hit.line_no}\n"
        f"    pattern: {hit.pattern.label}\n"
        f"    line:    {snippet}\n"
        f"    fix:     {hit.pattern.why}"
    )


def main() -> int:
    repo_root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], text=True,
        ).strip()
    )
    hits = scan(repo_root)
    if not hits:
        print("[public-mirror-safety] OK — no operator-private patterns "
              "detected in public-bound files.")
        return 0
    # Group by file so the PR author sees one block per offender.
    by_file: dict[str, list[Hit]] = {}
    for h in hits:
        by_file.setdefault(h.file, []).append(h)
    print(f"[public-mirror-safety] FAIL — {len(hits)} leak(s) across "
          f"{len(by_file)} file(s):")
    print()
    for file, file_hits in sorted(by_file.items()):
        for h in file_hits:
            print(_format_hit(h))
            print()
    print("These patterns would ship to the public Glad-Labs/poindexter "
          "mirror. Either:")
    print("  1. Fix the leak in the file (replace with placeholder), OR")
    print("  2. Add the file to _STRIP_DIR_PREFIXES / _STRIP_FILES if it's")
    print("     genuinely private (and mirror the change in "
          "scripts/sync-to-github.sh).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
