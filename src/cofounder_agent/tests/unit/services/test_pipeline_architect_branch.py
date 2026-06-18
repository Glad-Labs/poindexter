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
