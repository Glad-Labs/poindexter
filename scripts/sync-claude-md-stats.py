r"""Sync the source-truth stats in CLAUDE.md to current repo state.

Stats CLAUDE.md carries fall in two buckets:

1. **Source-truth** — derivable from the checked-in repo alone (service
   + test file counts, dashboard count). These drift the moment any of
   those land. This script updates them in place.
2. **DB-derived** — only visible from the live production database
   (post counts, embeddings totals, pipeline_tasks lifetime totals,
   app_settings totals). Out of scope here — owned by the companion
   ``sync_claude_md_db_stats.py``, which runs locally (where a DSN
   resolves) and is invoked by the daily ``claude-md-sync`` session.

Idempotent: re-running on already-fresh state produces zero changes.
Diff-only mode (``--check``) exits non-zero if drift would happen,
which the CI workflow uses to decide whether to open a sync PR.

Stat patterns are anchored on prose context so they only match the
sentence we want — a bare ``\d+`` regex would catch any number in the
file. Each replacement is logged so the PR description can list what
changed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"


def _glob_count(pattern: str) -> int:
    return len(list(ROOT.glob(pattern)))


def collect_stats() -> OrderedDict[str, int | str]:
    """Pull every source-truth metric in one pass."""
    services_dir = ROOT / "src/cofounder_agent/services"
    tests_dir = ROOT / "src/cofounder_agent/tests/unit"
    # Test files = files named test_*.py under tests/unit (pytest's
    # default discovery pattern). Counting by filename, not by ``def
    # test_`` lines, because most tests live inside classes and the
    # def is indented — a content-grep would undercount badly.
    test_files = [
        p for p in tests_dir.rglob("test_*.py")
        if p.name != "test_helpers.py"
    ]
    return OrderedDict([
        ("service_py_files", len(list(services_dir.rglob("*.py")))),
        ("test_files", len(test_files)),
        # No migration count: CLAUDE.md carries no "N migration files" claim
        # to sync, and the "Latest as of …: <file>.py" line needs narrative
        # reasoning (handled by the claude-md-sync session, not regex).
        ("grafana_dashboards", _glob_count(
            "infrastructure/grafana/dashboards/*.json",
        )),
    ])


def apply_to_claude_md(stats: OrderedDict[str, int | str]) -> tuple[str, list[str]]:
    r"""Return ``(new_text, changes)``. ``changes`` lists which lines
    were rewritten so the PR description can summarise.

    Each entry's pattern is anchored on surrounding prose so we don't
    rewrite an unrelated ``\d+`` elsewhere in the file.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    changes: list[str] = []

    def _sub(pattern: str, repl: str, label: str) -> None:
        nonlocal text
        new, n = re.subn(pattern, repl, text, count=1)
        if n and new != text:
            changes.append(label)
            text = new

    # "329 Python files under `src/cofounder_agent/services/`"
    _sub(
        r"\d+ Python files under `src/cofounder_agent/services/`",
        f"{stats['service_py_files']} Python files under `src/cofounder_agent/services/`",
        f"service_py_files ->{stats['service_py_files']}",
    )

    # "8,400+ Python unit tests across 369 test files"
    _sub(
        r"\d+ test files",
        f"{stats['test_files']} test files",
        f"test_files ->{stats['test_files']}",
    )

    # "8 Grafana dashboards (Mission Control / …)"
    _sub(
        r"\d+ Grafana dashboards",
        f"{stats['grafana_dashboards']} Grafana dashboards",
        f"grafana_dashboards ->{stats['grafana_dashboards']}",
    )

    # Note: ``Latest as of YYYY-MM-DD: `<migration>.py``` is NOT auto-
    # synced. The surrounding prose ("closes #N", "Lane X cutover seam",
    # etc.) describes WHICH migration we're calling out, and changing
    # just the filename leaves a misleading description. Migration
    # narrative needs LLM-level reasoning or a manual hand on the
    # tiller — out of scope for this regex-based sync.

    return text, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if CLAUDE.md would change; don't write.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the collected stats as JSON and exit (no file write).",
    )
    args = parser.parse_args()

    stats = collect_stats()
    if args.json:
        print(json.dumps(stats, indent=2))
        return 0

    new_text, changes = apply_to_claude_md(stats)
    original = CLAUDE_MD.read_text(encoding="utf-8")
    drift = new_text != original

    if args.check:
        if drift:
            print("CLAUDE.md drift detected:")
            for c in changes:
                print(f"  - {c}")
            return 1
        print("CLAUDE.md is in sync.")
        return 0

    if drift:
        CLAUDE_MD.write_text(new_text, encoding="utf-8")
        print("CLAUDE.md updated:")
        for c in changes:
            print(f"  - {c}")
    else:
        print("CLAUDE.md is in sync (no changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
