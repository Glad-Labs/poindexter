"""Branch-router compile tests (QA rescue cycle).

A node with a "branch": true out-edge gets a _goto-aware conditional router:
- _halt=True   -> END (halt always wins)
- _goto==target -> the branch target (the rescue node)
- otherwise    -> the default forward target
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from plugins.atom import AtomMeta
from services import pipeline_architect


def _meta(name: str, *, requires: tuple = (), produces: tuple = ()) -> AtomMeta:
    return AtomMeta(
        name=name, type="atom", version="1.0.0", description=name,
        requires=requires, produces=produces,
    )


def _compile_branch_graph(log: list[str], *, gate_fn):
    """gate -> (branch:rescue | default:cont); rescue -> END; cont -> END."""

    async def rescue_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("rescue")
        return {}

    async def cont_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("cont")
        return {}

    catalog = {
        "t.gate": _meta("t.gate"), "t.rescue": _meta("t.rescue"), "t.cont": _meta("t.cont"),
    }
    callables = {"t.gate": gate_fn, "t.rescue": rescue_fn, "t.cont": cont_fn}
    spec = {
        "name": "branch_test",
        "entry": "gate",
        "nodes": [
            {"id": "gate", "atom": "t.gate"},
            {"id": "rescue", "atom": "t.rescue"},
            {"id": "cont", "atom": "t.cont"},
        ],
        "edges": [
            {"from": "gate", "to": "rescue", "branch": True},
            {"from": "gate", "to": "cont"},
            {"from": "rescue", "to": "END"},
            {"from": "cont", "to": "END"},
        ],
    }
    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda n: catalog.get(n)),
        patch.object(pipeline_architect, "get_atom_callable", lambda n: callables.get(n)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return pipeline_architect.build_graph_from_spec(spec, pool=None).compile()


@pytest.mark.unit
class TestBranchRouter:
    async def test_goto_routes_to_branch_target(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": "rescue"}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t1"}})
        assert log == ["gate", "rescue"], log

    async def test_empty_goto_routes_to_default(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": ""}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t2"}})
        assert log == ["gate", "cont"], log

    async def test_halt_beats_goto(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            # Even with _goto set, _halt must win and route to END.
            return {"_halt": True, "_goto": "rescue"}

        compiled = _compile_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "t3"}})
        assert log == ["gate"], log


def _compile_multi_branch_graph(log: list[str], *, gate_fn):
    """gate -> (branch:rescue_a | branch:rescue_b | default:cont); each -> END.

    Two ``branch``-flagged out-edges from one source — the preview_gate shape
    (approve / regen_images / regen_text). The single-target ``branch_by_src``
    keeps only the LAST branch edge, so the real default (cont) becomes
    unreachable and an empty ``_goto`` mis-routes to a branch target.
    """

    async def rescue_a_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("rescue_a")
        return {}

    async def rescue_b_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("rescue_b")
        return {}

    async def cont_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("cont")
        return {}

    catalog = {
        "t.gate": _meta("t.gate"), "t.rescue_a": _meta("t.rescue_a"),
        "t.rescue_b": _meta("t.rescue_b"), "t.cont": _meta("t.cont"),
    }
    callables = {
        "t.gate": gate_fn, "t.rescue_a": rescue_a_fn,
        "t.rescue_b": rescue_b_fn, "t.cont": cont_fn,
    }
    spec = {
        "name": "multi_branch_test",
        "entry": "gate",
        "nodes": [
            {"id": "gate", "atom": "t.gate"},
            {"id": "rescue_a", "atom": "t.rescue_a"},
            {"id": "rescue_b", "atom": "t.rescue_b"},
            {"id": "cont", "atom": "t.cont"},
        ],
        "edges": [
            {"from": "gate", "to": "rescue_a", "branch": True},
            {"from": "gate", "to": "rescue_b", "branch": True},
            {"from": "gate", "to": "cont"},
            {"from": "rescue_a", "to": "END"},
            {"from": "rescue_b", "to": "END"},
            {"from": "cont", "to": "END"},
        ],
    }
    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda n: catalog.get(n)),
        patch.object(pipeline_architect, "get_atom_callable", lambda n: callables.get(n)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return pipeline_architect.build_graph_from_spec(spec, pool=None).compile()


@pytest.mark.unit
class TestMultiBranchRouter:
    """A source with MULTIPLE branch targets routes _goto to whichever matches,
    else the default forward edge (preview_gate: approve/regen_images/regen_text)."""

    async def test_goto_routes_to_first_branch_target(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": "rescue_a"}

        compiled = _compile_multi_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "mb1"}})
        assert log == ["gate", "rescue_a"], log

    async def test_goto_routes_to_second_branch_target(self):
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": "rescue_b"}

        compiled = _compile_multi_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "mb2"}})
        assert log == ["gate", "rescue_b"], log

    async def test_empty_goto_routes_to_default(self):
        # RED driver: with the single-target router the real default (cont) is
        # unreachable, so an empty _goto mis-routes to a branch target.
        log: list[str] = []

        async def gate(state):
            log.append("gate")
            return {"_goto": ""}

        compiled = _compile_multi_branch_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "mb3"}})
        assert log == ["gate", "cont"], log


def _compile_backward_loop_graph(log: list[str], *, gate_fn):
    """A -> B -> gate -> cont (forward), plus gate -> A / gate -> B as BACKWARD
    branch+loop edges — the real preview_gate shape (regen_text -> writer block,
    regen_images -> image block). The ``loop`` flag must exempt these backward
    edges from DAG/reachability validation while ``branch`` wires them via _goto.
    """

    async def a_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("A")
        return {}

    async def b_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("B")
        return {}

    async def cont_fn(state: dict[str, Any]) -> dict[str, Any]:
        log.append("cont")
        return {}

    catalog = {
        "t.a": _meta("t.a"), "t.b": _meta("t.b"),
        "t.gate": _meta("t.gate"), "t.cont": _meta("t.cont"),
    }
    callables = {"t.a": a_fn, "t.b": b_fn, "t.gate": gate_fn, "t.cont": cont_fn}
    spec = {
        "name": "backward_loop_test",
        "entry": "A",
        "nodes": [
            {"id": "A", "atom": "t.a"},
            {"id": "B", "atom": "t.b"},
            {"id": "gate", "atom": "t.gate"},
            {"id": "cont", "atom": "t.cont"},
        ],
        "edges": [
            {"from": "A", "to": "B"},
            {"from": "B", "to": "gate"},
            {"from": "gate", "to": "cont"},  # default forward (approve)
            {"from": "gate", "to": "A", "branch": True, "loop": True},  # regen_text
            {"from": "gate", "to": "B", "branch": True, "loop": True},  # regen_images
            {"from": "cont", "to": "END"},
        ],
    }
    with (
        patch.object(pipeline_architect, "get_atom_meta", lambda n: catalog.get(n)),
        patch.object(pipeline_architect, "get_atom_callable", lambda n: callables.get(n)),
        patch("plugins.registry.get_core_samples", return_value={"stages": []}),
    ):
        return pipeline_architect.build_graph_from_spec(spec, pool=None).compile()


@pytest.mark.unit
class TestBackwardBranchLoop:
    """preview_gate's regen edges point backward, so they are branch+loop. The
    compiler must accept them (loop = DAG-exempt) and route _goto to them."""

    async def test_backward_branch_loop_regen_images_loops_then_approves(self):
        # First pass: gate routes _goto="B" (regen_images) -> B re-runs only
        # (A untouched), back to gate; second pass approves -> cont.
        log: list[str] = []
        calls = {"n": 0}

        async def gate(state):
            log.append("gate")
            calls["n"] += 1
            return {"_goto": "B"} if calls["n"] == 1 else {"_goto": ""}

        compiled = _compile_backward_loop_graph(log, gate_fn=gate)
        await compiled.ainvoke({"task_id": "t"}, config={"configurable": {"thread_id": "bl1"}})
        assert log == ["A", "B", "gate", "B", "gate", "cont"], log
