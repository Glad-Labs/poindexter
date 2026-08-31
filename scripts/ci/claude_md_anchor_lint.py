"""Fail CI when a doc sync anchor stops matching (#2832).

Every count the two sync scripts maintain rides on a prose anchor.
Reword the prose and the regex silently stops matching — the number
freezes while still reading as current, and CLAUDE.md is what every
future session treats as ground truth. That happened twice in two days
(2026-07-26): #2820 reworded the ``app_settings`` bullet, and the next
nightly run refreshed the other counts while leaving that one frozen,
logging nothing.

This lint imports the anchor patterns FROM the sync scripts (never
restates them — a copy would drift and green falsely) and fails on any
zero-match against the checked-in file, naming the dead anchor and the
script that owns it. Covered sources:

* ``scripts/sync_claude_md_db_stats.py`` — ``COUNT_ANCHORS`` (5 DB-derived
  CLAUDE.md counts) + ``HEADER_RE`` (the Key Numbers freshness stamp) +
  ``README_ANCHORS`` (5 DB-derived README claims).
* ``scripts/sync-claude-md-stats.py`` — ``STAT_ANCHORS`` (3 repo-derived
  CLAUDE.md counts) + ``README_ANCHORS`` (3 test-count README claims);
  hyphenated filename, so it loads via importlib.

README.md joined CLAUDE.md as a target once its marketing stats went on
the same mechanism. It is the higher-stakes of the two: CLAUDE.md is read
by sessions that can sanity-check it, while README is the public front
door and ships verbatim to the mirror, where a frozen number is the first
thing a stranger reads.

Public-mirror behavior: CLAUDE.md is stripped from Glad-Labs/poindexter
but this lint (and the shared ``migrations-smoke.yml`` step that runs it)
is not, so a missing CLAUDE.md is a clean SKIP for that file's anchors.
README.md is NOT stripped, so the mirror still gets its anchors checked —
which is the half that matters most there. Stripping the lint instead
would break the mirror's workflow at the missing path, and a per-repo
step guard is the known inverted-``github.repository`` trap (the mirror
sync byte-rewrites repo names). Every target being absent is a FAILURE,
not a skip: per the repo's scan-floor doctrine, a check that scanned
nothing has not passed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
README_MD = ROOT / "README.md"

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


def collect_anchor_sources() -> list[tuple[str, str, str, re.Pattern[str]]]:
    """``(owner-script, target-filename, anchor-name, pattern)`` for every anchor."""
    from scripts import sync_claude_md_db_stats as db_sync  # type: ignore[attr-defined]

    repo_sync = _load_hyphenated(ROOT / "scripts" / "sync-claude-md-stats.py")

    db_owner = "scripts/sync_claude_md_db_stats.py"
    repo_owner = "scripts/sync-claude-md-stats.py"

    sources: list[tuple[str, str, str, re.Pattern[str]]] = []
    for name, pattern in db_sync.COUNT_ANCHORS.items():
        sources.append((db_owner, "CLAUDE.md", name, re.compile(pattern)))
    sources.append((db_owner, "CLAUDE.md", "key_numbers_header", db_sync.HEADER_RE))
    for name, pattern in db_sync.README_ANCHORS.items():
        sources.append((db_owner, "README.md", name, re.compile(pattern)))
    for name, pattern in repo_sync.STAT_ANCHORS.items():
        sources.append((repo_owner, "CLAUDE.md", name, re.compile(pattern)))
    for name, pattern in repo_sync.README_ANCHORS.items():
        sources.append((repo_owner, "README.md", name, re.compile(pattern)))
    return sources


def main() -> int:
    targets = {"CLAUDE.md": CLAUDE_MD, "README.md": README_MD}
    texts: dict[str, str] = {}
    for label, path in targets.items():
        if path.is_file():
            texts[label] = path.read_text(encoding="utf-8")
        else:
            # Public mirror strips CLAUDE.md; the lint is not stripped.
            print(f"claude_md_anchor_lint: {label} absent — skip its anchors.")

    if not texts:
        # Scan floor: no target present means the lint examined nothing, and
        # a check that scanned nothing has not passed. Both files vanishing
        # is a repo-layout break (a rename, a bad sync filter), not a state
        # any real checkout reaches.
        print(
            "claude_md_anchor_lint: NO TARGET FILE FOUND — neither CLAUDE.md "
            "nor README.md is present, so zero anchors were checked. That is "
            "a failure, not a pass: fix the paths in this lint or restore the "
            "files.",
        )
        return 1

    sources = collect_anchor_sources()
    dead: list[tuple[str, str, str]] = []
    checked = 0
    for owner, label, name, pattern in sources:
        text = texts.get(label)
        if text is None:
            continue
        checked += 1
        if not pattern.search(text):
            dead.append((owner, label, name))

    if dead:
        print("claude_md_anchor_lint: DEAD SYNC ANCHOR(S) — a rewording broke")
        print("the prose these counts ride on. The sync script will silently")
        print("stop updating them (the number freezes while still reading as")
        print("current). Either restore the wording or update the pattern in")
        print("the owning script:")
        for owner, label, name in dead:
            print(f"  - {name} in {label} (owned by {owner})")
        return 1

    print(
        f"claude_md_anchor_lint: clean — all {checked} anchors match "
        f"{' + '.join(texts)}.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
