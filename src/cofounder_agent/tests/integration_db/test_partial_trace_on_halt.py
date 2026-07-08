"""End-to-end guard: a run killed mid-graph leaves a partial ``atom_runs`` trace.

The console task-trace's load-bearing promise (spec §5.2) is that per-node rows
are written *incrementally*, as each node lands — so a run that dies before the
end-of-run batch (hard kill, hang, cancellation, or an ``ainvoke`` exception that
makes ``TemplateRunner.run`` return early) still leaves rows for the nodes that
finished, instead of nothing.

This runs a real compiled ``graph_def`` (via ``build_graph_from_spec``) whose
``record_sink`` is a ``_RecordingSink`` wired to ``persist_one_atom_run`` against
the disposable ``integration_db`` DB — the exact seam ``TemplateRunner.run``
builds. It then **deliberately never calls the end-of-run batch**, simulating a
kill before the batch runs. Any rows present were written incrementally.

Uses the real ``test_pool`` (persist acquires its own connection); the whole test
DB is dropped at session teardown, so session-scoped writes don't leak.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from plugins.atom import AtomMeta
from services import pipeline_architect
from services.atom_runs import persist_one_atom_run
from services.pipeline_architect import _RecordingSink
from services.template_runner import PipelineState

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


def _meta(name: str) -> AtomMeta:
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=(), produces=(),
    )


def _persist_closure(test_pool: Any, *, run_id: str, task_id: str):
    async def _persist(seq: int, rec: Any) -> None:
        await persist_one_atom_run(
            test_pool, run_id=run_id, task_id=task_id,
            template_slug="partial_trace_test", seq=seq, record=rec,
        )

    return _persist


def _compiled_three_node_graph(sink: _RecordingSink, *, raiser: str, log: list[str]):
    """a -> b -> c -> END, where node ``raiser`` raises when it runs."""

    def _fn(node: str):
        async def run(state: dict[str, Any]) -> dict[str, Any]:
            log.append(node)
            if node == raiser:
                raise RuntimeError(f"node {node} blew up mid-graph")
            return dict(state)

        return run

    catalog = {f"t.{n}": _meta(f"t.{n}") for n in ("a", "b", "c")}
    callables = {f"t.{n}": _fn(n) for n in ("a", "b", "c")}
    spec = {
        "name": "partial_trace_test",
        "entry": "a",
        "nodes": [
            {"id": "a", "atom": "t.a"},
            {"id": "b", "atom": "t.b"},
            {"id": "c", "atom": "t.c"},
        ],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "b", "to": "c"},
            {"from": "c", "to": "END"},
        ],
    }
    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda name: catalog.get(name)),
        patch.object(pipeline_architect, "get_atom_callable", lambda name: callables.get(name)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return pipeline_architect.build_graph_from_spec(
            spec, pool=None, record_sink=sink,
        ).compile()


async def test_last_node_halt_persists_completed_and_halted_nodes(test_pool):
    """3-node graph, node c raises. atom_runs carries rows 0/1 (completed) AND
    row 2 (the halted node) — all written incrementally, batch never called."""
    run_id = "trace-partial-last-node"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE run_id = $1", run_id)

    log: list[str] = []
    sink = _RecordingSink()
    sink.set_on_record(_persist_closure(test_pool, run_id=run_id, task_id="T-last"))
    compiled = _compiled_three_node_graph(sink, raiser="c", log=log)

    initial: PipelineState = {"task_id": "T-last"}  # type: ignore[typeddict-item]
    # A raising atom is caught by _wrap_atom (record + _halt), so ainvoke returns
    # normally rather than propagating — the halt still routes to END.
    await compiled.ainvoke(initial, config={"configurable": {"thread_id": run_id}})

    # Deliberately NOT calling persist_atom_runs (the end-of-run batch): whatever
    # is in atom_runs was persisted incrementally, as each node landed.
    async with test_pool.acquire() as c:
        rows = await c.fetch(
            "SELECT seq, atom, status FROM atom_runs WHERE run_id = $1 ORDER BY seq",
            run_id,
        )

    assert log == ["a", "b", "c"]  # all three ran; c raised
    assert [r["seq"] for r in rows] == [0, 1, 2]
    assert [r["atom"] for r in rows] == ["t.a", "t.b", "t.c"]
    assert rows[0]["status"] == "ok"
    assert rows[1]["status"] == "ok"
    assert rows[2]["status"] in ("halted", "error")  # the node that raised


async def test_middle_node_halt_stops_trace_at_kill_point(test_pool):
    """3-node graph, node b halts. node c never runs, so the partial trace has
    exactly rows 0/1 — it reflects what actually executed, no phantom node."""
    run_id = "trace-partial-middle-node"
    async with test_pool.acquire() as c:
        await c.execute("DELETE FROM atom_runs WHERE run_id = $1", run_id)

    log: list[str] = []
    sink = _RecordingSink()
    sink.set_on_record(_persist_closure(test_pool, run_id=run_id, task_id="T-mid"))
    compiled = _compiled_three_node_graph(sink, raiser="b", log=log)

    initial: PipelineState = {"task_id": "T-mid"}  # type: ignore[typeddict-item]
    await compiled.ainvoke(initial, config={"configurable": {"thread_id": run_id}})

    async with test_pool.acquire() as c:
        rows = await c.fetch(
            "SELECT seq, atom, status FROM atom_runs WHERE run_id = $1 ORDER BY seq",
            run_id,
        )

    assert log == ["a", "b"]  # c never ran — b halted the graph
    assert [r["seq"] for r in rows] == [0, 1]
    assert [r["atom"] for r in rows] == ["t.a", "t.b"]
    assert rows[1]["status"] in ("halted", "error")  # no seq-2 row was fabricated
