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

This script also owns the repo-derived half of the root **README.md**
marketing stats (the test-count badge and its two prose restatements).
README claims are floored to a round ``N+`` rather than written exactly —
rationale and the shared helpers live in ``scripts/lib_readme_stats.py``.

Stat patterns are anchored on prose context so they only match the
sentence we want — a bare ``\d+`` regex would catch any number in the
file. Each replacement is logged so the PR description can list what
changed, and an anchor that matches nothing is reported as a ``WARNING:``
rather than passing silently as "already correct" (#2832).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# The repo root has to be importable before ``scripts.lib_readme_stats``
# resolves: this script is launched by path (``python scripts/sync-...``),
# which puts ``scripts/`` — not the root — on sys.path.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.lib_readme_stats import (  # noqa: E402  (needs the path bootstrap above)
    FLOOR_STEPS,
    README_MD,
    floored,
    is_warning,
    shield_escape,
    substitute_anchored,
)

CLAUDE_MD = ROOT / "CLAUDE.md"

# The prose anchors each repo-derived stat rides on, keyed to match
# ``collect_stats``. Module-level so ``scripts/ci/claude_md_anchor_lint.py``
# can import and run them against the checked-in CLAUDE.md — a reword that
# kills an anchor now fails CI on the PR that breaks it, instead of silently
# freezing the count (#2832). The CLAUDE.md "Editing note" warns humans about
# these exact wordings; the lint makes the warning enforceable.
STAT_ANCHORS: OrderedDict[str, str] = OrderedDict([
    ("service_py_files", r"\d+ Python files under `src/cofounder_agent/services/`"),
    ("test_files", r"\d+ test files"),
    ("grafana_dashboards", r"\d+ Grafana dashboards"),
])

# The README.md anchors this script owns — the test count, which appears
# three times (a shields.io badge, a Key-features row, a Project-status
# bullet). Same lint contract as STAT_ANCHORS: module-level so
# ``scripts/ci/claude_md_anchor_lint.py`` imports and runs them against the
# checked-in README, so a reword fails CI on the PR that breaks it instead
# of freezing the number. The badge pattern matches the percent-encoded
# form shields.io needs (``17%2C000%2B``); the other two match plain prose.
README_ANCHORS: OrderedDict[str, str] = OrderedDict([
    ("test_functions_badge", r"tests-[\d%C]+%2B-brightgreen"),
    ("test_functions_feature_row", r"\*\*[\d,]+\+ tests\*\*"),
    ("test_functions_status_bullet", r"[\d,]+\+ unit tests passing in CI"),
    # The canonical_blog graph's shape. NOT floored (see apply_to_readme):
    # these are exact structural facts, and "40+ nodes" would be vaguer AND
    # less true than "46".
    ("canonical_blog_nodes", r"[\d,]+ nodes covering"),
    ("qa_rails_curation", r"survive [\d,]+ QA rails"),
    ("qa_rails_dag", r"the [\d,]+ QA rails"),
])


def _glob_count(pattern: str) -> int:
    return len(list(ROOT.glob(pattern)))


# ``def test_x`` / ``async def test_x`` at any indentation — most tests live
# inside classes, so the def is indented and a column-0 grep undercounts badly.
_TEST_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+test_", re.MULTILINE)


def _count_test_functions(test_files: list[Path]) -> int:
    """Approximate the suite size by counting ``def test_`` declarations.

    A static count, not a pytest collection: it can't expand
    ``@pytest.mark.parametrize`` (so it undercounts what actually runs) and
    it counts a commented-out or skipped def (so it overcounts a little the
    other way). Both errors are fractions of a percent — the real 2026-08-12
    run collected 17,212 against 17,238 declarations here — and the only
    consumer floors to the nearest thousand, so precision beyond this buys
    nothing. Collecting for real would need the full test dependency tree
    installed in CI, which is exactly the cost this avoids.
    """
    return sum(
        len(_TEST_DEF_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
        for p in test_files
    )


def _load_canonical_blog_spec() -> dict[str, Any]:
    """Read ``CANONICAL_BLOG_GRAPH_DEF`` out of its module without importing it.

    The spec's own docstring promises "pure data — NO imports beyond typing",
    which is what makes an ``ast.literal_eval`` safe here. Parsing rather than
    importing is deliberate: this script runs on a bare ``setup-python`` step
    with no ``pip install``, so ``import services.canonical_blog_spec`` would
    drag the package's dependency tree into a job that has none.

    Raises rather than returning a default. A spec that cannot be read must
    not quietly become "0 nodes covering …" in the public README — a red CI
    job is the correct outcome (`feedback_no_silent_defaults`).
    """
    path = ROOT / "src/cofounder_agent/services/canonical_blog_spec.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if any(
            isinstance(t, ast.Name) and t.id == "CANONICAL_BLOG_GRAPH_DEF"
            for t in targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, dict) or "nodes" not in value:
                raise ValueError(f"CANONICAL_BLOG_GRAPH_DEF in {path} is not a graph spec")
            return value
    raise ValueError(f"no CANONICAL_BLOG_GRAPH_DEF assignment found in {path}")


# qa.aggregate is the gate DECISION and qa.rewrite is the rescue revision —
# neither is a rail that scores the draft, so neither counts toward the rail
# total the README advertises. Keep this in step with CLAUDE.md's
# "canonical_blog wires 14 of them".
_NON_RAIL_QA_ATOMS = frozenset({"qa.aggregate", "qa.rewrite"})


def _count_qa_rails(spec: dict[str, Any]) -> int:
    return sum(
        1
        for node in spec["nodes"]
        if str(node.get("atom", "")).startswith("qa.")
        and node["atom"] not in _NON_RAIL_QA_ATOMS
    )


def collect_stats() -> OrderedDict[str, int | str]:
    """Pull every source-truth metric in one pass."""
    services_dir = ROOT / "src/cofounder_agent/services"
    tests_dir = ROOT / "src/cofounder_agent/tests/unit"
    # Test files = files named test_*.py under tests/unit (pytest's
    # default discovery pattern).
    test_files = [
        p for p in tests_dir.rglob("test_*.py")
        if p.name != "test_helpers.py"
    ]
    spec = _load_canonical_blog_spec()
    return OrderedDict([
        ("service_py_files", len(list(services_dir.rglob("*.py")))),
        ("test_files", len(test_files)),
        ("test_functions", _count_test_functions(test_files)),
        # No migration count: CLAUDE.md carries no "N migration files" claim
        # to sync, and the "Latest as of …: <file>.py" line needs narrative
        # reasoning (handled by the claude-md-sync session, not regex).
        ("grafana_dashboards", _glob_count(
            "infrastructure/grafana/dashboards/*.json",
        )),
        ("canonical_blog_nodes", len(spec["nodes"])),
        ("canonical_blog_qa_rails", _count_qa_rails(spec)),
    ])


def apply_to_claude_md(stats: OrderedDict[str, int | str]) -> tuple[str, list[str]]:
    r"""Return ``(new_text, changes)``. ``changes`` lists which lines
    were rewritten so the PR description can summarise.

    Each entry's pattern is anchored on surrounding prose so we don't
    rewrite an unrelated ``\d+`` elsewhere in the file.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")

    # Patterns live in STAT_ANCHORS (module level, lint-imported — #2832):
    #   "329 Python files under `src/cofounder_agent/services/`"
    #   "8,400+ Python unit tests across 369 test files"
    #   "8 Grafana dashboards (Mission Control / …)"
    text, changes = substitute_anchored(text, [
        (
            "service_py_files",
            STAT_ANCHORS["service_py_files"],
            f"{stats['service_py_files']} Python files under "
            "`src/cofounder_agent/services/`",
        ),
        ("test_files", STAT_ANCHORS["test_files"], f"{stats['test_files']} test files"),
        (
            "grafana_dashboards",
            STAT_ANCHORS["grafana_dashboards"],
            f"{stats['grafana_dashboards']} Grafana dashboards",
        ),
    ])

    # Note: ``Latest as of YYYY-MM-DD: `<migration>.py``` is NOT auto-
    # synced. The surrounding prose ("closes #N", "Lane X cutover seam",
    # etc.) describes WHICH migration we're calling out, and changing
    # just the filename leaves a misleading description. Migration
    # narrative needs LLM-level reasoning or a manual hand on the
    # tiller — out of scope for this regex-based sync.

    return text, changes


def apply_to_readme(
    stats: OrderedDict[str, int | str],
    text: str | None = None,
) -> tuple[str, list[str]]:
    """Return ``(new_text, changes)`` for the README claims this script owns.

    Two kinds, and they are formatted differently on purpose:

    * The **test count** is floored to a round ``N+`` (see
      ``scripts/lib_readme_stats``) — it moves every day, and a claim to
      strangers should understate rather than churn.
    * The **canonical_blog graph shape** (node count, QA-rail count) is
      written EXACTLY. Flooring a small structural fact would make it both
      vaguer and less true: "40+ nodes" is a worse sentence than "46 nodes",
      and the graph changes a few times a year, so there is no churn to damp.

    Everything else in README's stat surface is DB-derived and belongs to
    ``sync_claude_md_db_stats.py``.

    Pass ``text`` to operate on a string in-memory (used by tests);
    otherwise the on-disk README.md is read.
    """
    current = README_MD.read_text(encoding="utf-8") if text is None else text
    claim = floored(int(stats["test_functions"]), FLOOR_STEPS["test_functions"])
    nodes = stats["canonical_blog_nodes"]
    rails = stats["canonical_blog_qa_rails"]
    return substitute_anchored(current, [
        # [![Tests](https://img.shields.io/badge/tests-17%2C000%2B-brightgreen)]
        (
            "test_functions_badge",
            README_ANCHORS["test_functions_badge"],
            f"tests-{shield_escape(claim)}-brightgreen",
        ),
        # | **17,000+ tests** | Unit coverage across all services, … |
        (
            "test_functions_feature_row",
            README_ANCHORS["test_functions_feature_row"],
            f"**{claim} tests**",
        ),
        # - 17,000+ unit tests passing in CI on every push, …
        (
            "test_functions_status_bullet",
            README_ANCHORS["test_functions_status_bullet"],
            f"{claim} unit tests passing in CI",
        ),
        # "… stored in the database — 46 nodes covering research, writing, …"
        (
            "canonical_blog_nodes",
            README_ANCHORS["canonical_blog_nodes"],
            f"{nodes} nodes covering",
        ),
        # "… then makes each one survive 14 QA rails — cross-model critics, …"
        (
            "qa_rails_curation",
            README_ANCHORS["qa_rails_curation"],
            f"survive {rails} QA rails",
        ),
        # "… image generation, the 14 QA rails, SEO, and publish."
        (
            "qa_rails_dag",
            README_ANCHORS["qa_rails_dag"],
            f"the {rails} QA rails",
        ),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if CLAUDE.md or README.md would change; don't write.",
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

    results: list[tuple[Path, str, list[str]]] = [
        (CLAUDE_MD, *apply_to_claude_md(stats)),
    ]
    if README_MD.is_file():
        results.append((README_MD, *apply_to_readme(stats)))
    else:
        # Not expected on glad-labs-stack (README.md is tracked). Loud rather
        # than silent: a vanished target must never read as "already in sync".
        print(f"WARNING: {README_MD.name} not found — its stats were not synced.")

    changes: list[str] = []
    dirty: list[tuple[Path, str]] = []
    for path, new_text, file_changes in results:
        changes.extend(file_changes)
        if new_text != path.read_text(encoding="utf-8"):
            dirty.append((path, new_text))

    # A dead anchor produces no diff by construction, so it would otherwise be
    # reported as "in sync" — the exact #2832 failure. Surface it on every
    # path. The hard gate is scripts/ci/claude_md_anchor_lint.py, which fails
    # CI on the PR that breaks the wording; this is the runtime echo of it.
    warnings = [c for c in changes if is_warning(c)]
    names = " + ".join(path.name for path, _, _ in results)

    if args.check:
        for warning in warnings:
            print(warning)
        if dirty:
            print("Stat drift detected:")
            for change in changes:
                print(f"  - {change}")
            return 1
        print(f"{names} stats are in sync.")
        return 0

    for path, new_text in dirty:
        path.write_text(new_text, encoding="utf-8")

    if dirty:
        print(f"{', '.join(path.name for path, _ in dirty)} updated:")
    else:
        print(f"{names} stats are in sync (no changes).")
    for change in changes:
        print(f"  - {change}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
