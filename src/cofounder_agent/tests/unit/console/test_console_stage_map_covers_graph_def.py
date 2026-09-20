"""CI gate: the operator console's pipeline stage map must cover every node of
the live ``canonical_blog`` graph_def.

Background (the bug this prevents). ``console/js/data.js`` carries a
``pipeline.stages`` table mapping each graph node id to one of the seven display
blocks. ``withLiveCounts()`` in ``console/js/app.jsx`` resolves a running task's
``stage`` through it::

    const block = nodeToBlock[r.stage];
    if (block) counts[block] = (counts[block] || 0) + 1;

An unmapped node therefore falls through the ``if`` and the task is counted in
NO block — it does not land in a fallback bucket, it simply disappears from the
strip. Nothing about that is visible: the panel renders, the other counts look
plausible, and the operator reads an undercount as an idle pipeline.

The table was hand-maintained and drifted. Measured on 2026-09-19 it mapped 35
of the spec's 49 nodes, and **22.1% of the previous 30 days' ``canonical_blog``
atom runs (521 of 2,362) landed on a node it did not know** — including every
run of ``qa_title_coherence``, ``qa_content_originality``, ``qa_self_claim``,
``preview_gate`` and ``social_generate_drafts``. It also still listed
``writer_self_review``, a node deleted on 2026-08-28 when it was split into
``detect_contradictions`` + ``revise_contradictions``.

Both directions are gated here, because both are real drift:

* a spec node missing from the console is a blind spot in the counts;
* a console node absent from the spec is a fossil that will never match, and is
  the tell that someone renamed a node without updating this surface.

The console is a Pro-tier overlay stripped from the public mirror
(``scripts/sync-to-github.sh``), so this module SKIPS when that tree is absent —
the documented ``uv_lock_version_lint`` precedent, where a legitimately-stripped
directory must not be confused with a disarmed gate. When the tree IS present,
extracting zero nodes from either side is a hard failure rather than a pass: a
check that scanned nothing has not passed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from poindexter.services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF


# Anchor on a sentinel rather than a parents[N] depth: the poindexter#1046
# namespace move pushed every file one level deeper and a baked-in depth would
# have silently pointed this gate at the wrong tree.
def _console_js_dir() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "console" / "js" / "data.js"
        if candidate.exists():
            return candidate.parent
        # Stop climbing once we're above the backend package root.
        if (parent / "pyproject.toml").exists() and (parent / "main.py").exists():
            return None
    return None


_CONSOLE_JS = _console_js_dir()

pytestmark = pytest.mark.skipif(
    _CONSOLE_JS is None,
    reason="operator console overlay not present (stripped from the public mirror)",
)


def _spec_node_ids() -> set[str]:
    return {n["id"] for n in CANONICAL_BLOG_GRAPH_DEF["nodes"]}


def _console_stage_map() -> dict[str, str]:
    """node id -> block name, parsed out of data.js `pipeline.stages`."""
    # pytestmark skips when this is None; assert so the type narrows too.
    assert _CONSOLE_JS is not None
    src = (_CONSOLE_JS / "data.js").read_text(encoding="utf-8")
    start = src.index("    stages: [")
    end = src.index("    perDay: [", start)
    block = src[start:end]

    mapping: dict[str, str] = {}
    for entry in re.finditer(r"name:\s*'([a-z0-9_]+)',\s*nodes:\s*\[(.*?)\]", block, re.DOTALL):
        name, body = entry.group(1), entry.group(2)
        for node in re.findall(r"'([a-z0-9_]+)'", body):
            mapping[node] = name
    return mapping


def test_console_stage_map_is_parseable() -> None:
    """Scan floor. Zero nodes parsed means the format moved, not that it passed."""
    spec = _spec_node_ids()
    mapping = _console_stage_map()

    assert len(spec) >= 40, f"graph_def spec yielded only {len(spec)} nodes — parse broke"
    assert len(mapping) >= 40, (
        f"parsed only {len(mapping)} nodes out of console/js/data.js — the "
        "`pipeline.stages` shape changed and this gate is reading nothing. "
        "Fix the parser; do not delete the assertion."
    )


def test_every_graph_def_node_is_mapped_to_a_console_block() -> None:
    """A node the console can't place is a task it silently stops counting."""
    missing = sorted(_spec_node_ids() - set(_console_stage_map()))

    assert not missing, (
        "canonical_blog nodes missing from the operator console's stage map: "
        f"{missing}\n\n"
        "withLiveCounts() (console/js/app.jsx) counts a running task only when "
        "its stage resolves to a block, so a task parked at one of these is "
        "counted in NO block and vanishes from the pipeline strip.\n"
        "Fix: add each node to the matching block in `pipeline.stages` in "
        "src/cofounder_agent/console/js/data.js."
    )


def test_console_stage_map_has_no_nodes_the_graph_def_dropped() -> None:
    """A fossil node can never match a live stage — the tell for a rename."""
    stale = sorted(set(_console_stage_map()) - _spec_node_ids())

    assert not stale, (
        "operator console stage map references nodes that canonical_blog no "
        f"longer has: {stale}\n\n"
        "These can never match a running task's stage. This is what a node "
        "rename looks like from here (writer_self_review outlived its 2026-08-28 "
        "split into detect_contradictions + revise_contradictions by weeks).\n"
        "Fix: remove or rename them in `pipeline.stages` in "
        "src/cofounder_agent/console/js/data.js."
    )
