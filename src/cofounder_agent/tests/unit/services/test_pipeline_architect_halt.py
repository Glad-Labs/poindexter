"""Regression test: compiled graph_def must short-circuit when a node sets _halt=True.

Glad-Labs/poindexter#721 — "node `_halt=True` doesn't short-circuit; terminal
state = load-bearing DB write, not a halt."

The build_graph_from_spec compiler inserts _halt_router_single conditional edges
after every node so that if a node sets _halt=True in state, the graph routes to
END instead of the declared successor. This test proves that contract holds end-
to-end on a compiled graph (not just that the router function exists).

Phase 1 (pre-fix): if this test fails it proves the bug is real.
Phase 2 (post-fix): this test must pass and remain green.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langgraph.graph import END

from plugins.atom import AtomMeta
from services import pipeline_architect
from services.template_runner import PipelineState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta(name: str, *, requires: tuple = (), produces: tuple = ()) -> AtomMeta:
    return AtomMeta(
        name=name,
        type="atom",
        version="1.0.0",
        description=name,
        requires=requires,
        produces=produces,
    )


def _build_two_node_graph(
    execution_log: list[str],
    *,
    node_a_halts: bool,
) -> Any:
    """Compile a two-node spec where node_a optionally sets _halt=True.

    node_a --> node_b --> END

    When node_a sets _halt=True, the compiler's halt-aware conditional edge
    should route directly to END, skipping node_b entirely.
    """

    async def node_a_fn(state: dict[str, Any]) -> dict[str, Any]:
        execution_log.append("node_a")
        if node_a_halts:
            return {**state, "_halt": True, "_halt_reason": "test halt from node_a"}
        return dict(state)

    async def node_b_fn(state: dict[str, Any]) -> dict[str, Any]:
        execution_log.append("node_b")
        return dict(state)

    catalog = {
        "test.node_a": _meta("test.node_a"),
        "test.node_b": _meta("test.node_b"),
    }

    spec = {
        "name": "halt_test",
        "entry": "node_a",
        "nodes": [
            {"id": "node_a", "atom": "test.node_a"},
            {"id": "node_b", "atom": "test.node_b"},
        ],
        "edges": [
            {"from": "node_a", "to": "node_b"},
            {"from": "node_b", "to": "END"},
        ],
    }

    callables = {
        "test.node_a": node_a_fn,
        "test.node_b": node_b_fn,
    }

    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda name: catalog.get(name)),
        patch.object(pipeline_architect, "get_atom_callable", lambda name: callables.get(name)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        graph = pipeline_architect.build_graph_from_spec(spec, pool=None)

    return graph.compile()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHaltShortCircuit:
    """Regression suite for #721 — _halt=True must short-circuit compiled graphs."""

    async def test_no_halt_runs_both_nodes(self) -> None:
        """Baseline: when no halt is set, both node_a and node_b run."""
        log: list[str] = []
        compiled = _build_two_node_graph(log, node_a_halts=False)

        initial: PipelineState = {"task_id": "test-no-halt"}  # type: ignore[typeddict-item]
        await compiled.ainvoke(initial, config={"configurable": {"thread_id": "test-no-halt"}})

        assert log == ["node_a", "node_b"], (
            f"Expected both nodes to run without _halt, got: {log}"
        )

    async def test_halt_skips_node_b(self) -> None:
        """Core regression: when node_a sets _halt=True, node_b must NOT run.

        This is the exact failure pattern documented in the operational note
        (atom_runs evidence tasks 574f9235 / 69caeedf): qa.aggregate (seq 19)
        set _halt=True on reject yet seo.* and finalize_task (seq 20-26)
        still ran.
        """
        log: list[str] = []
        compiled = _build_two_node_graph(log, node_a_halts=True)

        initial: PipelineState = {"task_id": "test-halt"}  # type: ignore[typeddict-item]
        final_state = await compiled.ainvoke(
            initial, config={"configurable": {"thread_id": "test-halt"}}
        )

        assert "node_a" in log, "node_a must run (it sets _halt)"
        assert "node_b" not in log, (
            f"node_b ran after _halt=True was set — _halt short-circuit is broken! "
            f"Execution log: {log}"
        )
        # The halt flag and reason must be preserved in final state.
        assert final_state.get("_halt") is True
        assert "test halt" in (final_state.get("_halt_reason") or "")

    async def test_halt_on_second_node_is_honored(self) -> None:
        """Edge case: a three-node graph where node_b (middle node) halts.
        node_c must not run.
        """
        log: list[str] = []

        async def node_a_fn(state: dict[str, Any]) -> dict[str, Any]:
            log.append("node_a")
            return dict(state)

        async def node_b_fn(state: dict[str, Any]) -> dict[str, Any]:
            log.append("node_b")
            return {**state, "_halt": True, "_halt_reason": "halt from node_b"}

        async def node_c_fn(state: dict[str, Any]) -> dict[str, Any]:
            log.append("node_c")
            return dict(state)

        catalog = {
            "test.node_a": _meta("test.node_a"),
            "test.node_b": _meta("test.node_b"),
            "test.node_c": _meta("test.node_c"),
        }
        callables = {
            "test.node_a": node_a_fn,
            "test.node_b": node_b_fn,
            "test.node_c": node_c_fn,
        }
        spec = {
            "name": "halt_middle_test",
            "entry": "node_a",
            "nodes": [
                {"id": "node_a", "atom": "test.node_a"},
                {"id": "node_b", "atom": "test.node_b"},
                {"id": "node_c", "atom": "test.node_c"},
            ],
            "edges": [
                {"from": "node_a", "to": "node_b"},
                {"from": "node_b", "to": "node_c"},
                {"from": "node_c", "to": "END"},
            ],
        }

        with (
            patch.object(pipeline_architect, "get_atom_meta", lambda name: catalog.get(name)),
            patch.object(pipeline_architect, "get_atom_callable", lambda name: callables.get(name)),
            patch("plugins.registry.get_core_samples", return_value={"stages": []}),
        ):
            compiled = pipeline_architect.build_graph_from_spec(spec, pool=None).compile()

        initial: PipelineState = {"task_id": "test-halt-middle"}  # type: ignore[typeddict-item]
        await compiled.ainvoke(
            initial, config={"configurable": {"thread_id": "test-halt-middle"}}
        )

        assert log == ["node_a", "node_b"], (
            f"Expected only node_a + node_b; node_c ran despite _halt=True: {log}"
        )

    async def test_halt_reason_propagates_to_final_state(self) -> None:
        """The _halt_reason string must appear in final_state so callers can
        read the reject reason without querying the DB."""
        log: list[str] = []
        compiled = _build_two_node_graph(log, node_a_halts=True)

        initial: PipelineState = {"task_id": "test-halt-reason"}  # type: ignore[typeddict-item]
        final = await compiled.ainvoke(
            initial, config={"configurable": {"thread_id": "test-halt-reason"}}
        )

        assert final.get("_halt_reason") is not None, (
            "_halt_reason must be present in final state so callers can identify why "
            "the graph halted without querying the DB"
        )
