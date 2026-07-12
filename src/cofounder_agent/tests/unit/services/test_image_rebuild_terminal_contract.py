"""Contract: image_rebuild finalizes its job row to a decided-terminal status.

Regression guard for the in_progress-stranding loop — the graph's terminal node
must set a status that post_pipeline_actions treats as decided AND that the
stale-sweep won't re-claim. Also proves target_status is declared in
PipelineState (the #753 seed-time schema gate).
"""
from __future__ import annotations

import services.pipeline_architect as pa
from services.atom_registry import discover
from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF
from services.post_pipeline_actions import _DECIDED_NON_REJECTED_STATUSES


def _node_by_id(spec: dict) -> dict[str, dict]:
    return {n["id"]: n for n in spec["nodes"]}


def test_terminal_node_is_set_task_status_to_END():
    edges = IMAGE_REBUILD_GRAPH_DEF["edges"]
    to_end = [e["from"] for e in edges if e["to"] == "END"]
    assert to_end == ["finalize"], f"expected single terminal 'finalize', got {to_end}"
    finalize = _node_by_id(IMAGE_REBUILD_GRAPH_DEF)["finalize"]
    assert finalize["atom"] == "atoms.set_task_status"


def test_terminal_target_status_is_decided_and_completed():
    finalize = _node_by_id(IMAGE_REBUILD_GRAPH_DEF)["finalize"]
    target = finalize["config"]["target_status"]
    assert target == "completed"
    # THE regression guard: the declared terminal must be one the post-pipeline
    # success-path guard recognizes, or the loop/side-effect bug returns.
    assert target in _DECIDED_NON_REJECTED_STATUSES


def test_spec_is_raw_unstamped():
    assert all(
        "_contract_fp" not in n for n in IMAGE_REBUILD_GRAPH_DEF["nodes"]
    ), "spec must be raw (unstamped) so the boot self-heal re-stamps it"


def test_target_status_declared_in_pipeline_state():
    """_validate_graph_schema raises if any atom requires/produces a key not in
    PipelineState. Passing here proves target_status was added to PipelineState."""
    discover()
    pa._validate_graph_schema(IMAGE_REBUILD_GRAPH_DEF)  # must not raise
