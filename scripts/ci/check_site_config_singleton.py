#!/usr/bin/env python3
"""Guardrail: prevent NEW imports of the ``services.site_config.site_config`` singleton.

Glad-Labs/glad-labs-stack#330 — incremental retirement of the module-level
singleton in favor of the DI seam (``Depends(get_site_config_dependency)``,
constructor injection, ``context.get("site_config")``).

Until the sweep is complete, ~114 callers still import the singleton. We
freeze the surface here so new code uses the DI seam and the allowlist
shrinks monotonically.

CHECKS

1. **No new singleton imports** — every ``from services.site_config import
   site_config`` and ``from cofounder_agent.services.site_config import
   site_config`` is matched against ``ALLOWLIST_PATH``. New offenders fail
   the build.
2. **Allowlist is current** — files in the allowlist that no longer have
   the import are reported as drift; run with ``--update`` to regenerate.
3. **Hardcoded exempts** — ``services/site_config.py`` (defines it) and
   ``main.py`` (runs the lifespan shim) are always allowed.

Exit codes:
    0 — clean (or --update succeeded)
    1 — new offender or stale allowlist
    2 — script error

Usage:
    python scripts/ci/check_site_config_singleton.py
    python scripts/ci/check_site_config_singleton.py --update    # regenerate allowlist
    python scripts/ci/check_site_config_singleton.py --strict    # treat drift as failure
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "cofounder_agent"
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "ci" / ".site_config_singleton_allowlist.txt"

EXEMPT = {
    # Defines the singleton.
    "src/cofounder_agent/services/site_config.py",
    # Runs the lifespan shim that re-points the singleton at the DB-loaded
    # instance — see CLAUDE.md "DB-first configuration".
    "src/cofounder_agent/main.py",
}

IMPORT_PATTERNS = [
    re.compile(r"^\s*from\s+services\.site_config\s+import\s+.*\bsite_config\b", re.MULTILINE),
    re.compile(r"^\s*from\s+cofounder_agent\.services\.site_config\s+import\s+.*\bsite_config\b", re.MULTILINE),
]


def find_offenders() -> set[str]:
    """Return repo-relative POSIX paths of files importing the singleton."""
    offenders: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pat.search(text) for pat in IMPORT_PATTERNS):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in EXEMPT:
                continue
            offenders.add(rel)
    return offenders


def load_allowlist() -> set[str]:
    if not ALLOWLIST_PATH.exists():
        return set()
    return {
        line.strip()
        for line in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def write_allowlist(paths: set[str]) -> None:
    header = (
        "# Allowlist for scripts/ci/check_site_config_singleton.py.\n"
        "# Each line is a repo-relative path that imports the legacy\n"
        "# `services.site_config.site_config` singleton. Driven by\n"
        "# Glad-Labs/glad-labs-stack#330 — the list shrinks as callers\n"
        "# migrate to the DI seam. New entries fail CI.\n"
    )
    body = "\n".join(sorted(paths))
    ALLOWLIST_PATH.write_text(header + body + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="Regenerate allowlist from current state and exit 0.")
    parser.add_argument("--strict", action="store_true",
                        help="Fail when allowlist contains paths no longer importing the singleton.")
    args = parser.parse_args()

    offenders = find_offenders()

    if args.update:
        write_allowlist(offenders)
        print(f"[site-config-guard] wrote {len(offenders)} entries to {ALLOWLIST_PATH.name}")
        return 0

    allowlist = load_allowlist()
    if not allowlist:
        print(
            f"[site-config-guard] ERROR: allowlist {ALLOWLIST_PATH} not found.\n"
            "Run `python scripts/ci/check_site_config_singleton.py --update` to seed it.",
            file=sys.stderr,
        )
        return 2

    new_offenders = sorted(offenders - allowlist)
    stale = sorted(allowlist - offenders)

    if new_offenders:
        print("[site-config-guard] FAIL — new singleton imports detected:", file=sys.stderr)
        for path in new_offenders:
            print(f"  + {path}", file=sys.stderr)
        print(
            "\nUse the DI seam instead — see CLAUDE.md 'DB-first configuration'.\n"
            "Route handlers: `Depends(get_site_config_dependency)`.\n"
            "Services: accept `site_config` in __init__.\n"
            "Pipeline stages: `context.get('site_config')`.\n"
            "If this import is unavoidable, add the path to the allowlist with\n"
            "`python scripts/ci/check_site_config_singleton.py --update` and\n"
            "explain in the PR description.",
            file=sys.stderr,
        )
        return 1

    if stale:
        msg = (
            f"[site-config-guard] {len(stale)} file(s) migrated off the singleton — "
            "allowlist is stale:\n  " + "\n  ".join(f"- {p}" for p in stale) +
            "\n\nRun `python scripts/ci/check_site_config_singleton.py --update` to refresh."
        )
        if args.strict:
            print(msg, file=sys.stderr)
            return 1
        print(msg)

    print(f"[site-config-guard] OK — {len(offenders)} known callers, no new offenders.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
