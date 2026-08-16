"""Fail CI when a CLAUDE.md sync anchor stops matching (#2832).

Every count the two CLAUDE.md sync scripts maintain rides on a prose
anchor. Reword the prose and the regex silently stops matching — the
number freezes while still reading as current, and CLAUDE.md is what
every future session treats as ground truth. That happened twice in two
days (2026-07-26): #2820 reworded the ``app_settings`` bullet, and the
next nightly run refreshed the other counts while leaving that one
frozen, logging nothing.

This lint imports the anchor patterns FROM the sync scripts (never
restates them — a copy would drift and green falsely) and fails on any
zero-match against the checked-in CLAUDE.md, naming the dead anchor and
the script that owns it. Covered sources:

* ``scripts/sync_claude_md_db_stats.py`` — ``COUNT_ANCHORS`` (5 DB-derived
  counts) + ``HEADER_RE`` (the Key Numbers freshness stamp).
* ``scripts/sync-claude-md-stats.py`` — ``STAT_ANCHORS`` (3 repo-derived
  counts; hyphenated filename, so it loads via importlib).

Public-mirror behavior: CLAUDE.md is stripped from Glad-Labs/poindexter
but this lint (and the shared ``migrations-smoke.yml`` step that runs it)
is not, so a missing CLAUDE.md is a clean SKIP, not a failure. Stripping
the lint instead would break the mirror's workflow at the missing path,
and a per-repo step guard is the known inverted-``github.repository``
trap (the mirror sync byte-rewrites repo names).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_hyphenated(path: Path):
    """Import a script whose filename isn't a legal module name."""
    spec = importlib.util.spec_from_file_location(
        path.stem.replace("-", "_"), path
    )
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect_anchor_sources() -> list[tuple[str, str, re.Pattern[str]]]:
    """(owner-script, anchor-name, compiled-pattern) for every sync anchor."""
    from scripts import sync_claude_md_db_stats as db_sync  # type: ignore[attr-defined]

    repo_sync = _load_hyphenated(ROOT / "scripts" / "sync-claude-md-stats.py")

    sources: list[tuple[str, str, re.Pattern[str]]] = []
    for name, pattern in db_sync.COUNT_ANCHORS.items():
        sources.append(
            ("scripts/sync_claude_md_db_stats.py", name, re.compile(pattern))
        )
    sources.append(
        ("scripts/sync_claude_md_db_stats.py", "key_numbers_header", db_sync.HEADER_RE)
    )
    for name, pattern in repo_sync.STAT_ANCHORS.items():
        sources.append(
            ("scripts/sync-claude-md-stats.py", name, re.compile(pattern))
        )
    return sources


def main() -> int:
    if not CLAUDE_MD.is_file():
        # Public mirror: CLAUDE.md is stripped, the lint is not. Nothing to
        # check — and failing here would red every mirror sync.
        print("claude_md_anchor_lint: CLAUDE.md absent (public mirror) — skip.")
        return 0

    text = CLAUDE_MD.read_text(encoding="utf-8")
    dead: list[tuple[str, str]] = []
    for owner, name, pattern in collect_anchor_sources():
        if not pattern.search(text):
            dead.append((owner, name))

    if dead:
        print("claude_md_anchor_lint: DEAD SYNC ANCHOR(S) — a CLAUDE.md")
        print("rewording broke the prose these counts ride on. The sync")
        print("script will silently stop updating them (the number freezes")
        print("while still reading as current). Either restore the wording")
        print("or update the pattern in the owning script:")
        for owner, name in dead:
            print(f"  - {name} (owned by {owner})")
        return 1

    n = len(collect_anchor_sources())
    print(f"claude_md_anchor_lint: clean — all {n} anchors match CLAUDE.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
