"""Registry discovery + spec validation for the qa.* rail atoms
(atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services import pipeline_architect
from services.atom_registry import discover, get_atom_callable, get_atom_meta

_RAILS = (
    "qa.programmatic", "qa.deepeval", "qa.guardrails", "qa.ragas", "qa.critic",
    "qa.vision",
)
_ALL = _RAILS + ("qa.aggregate",)


@pytest.mark.unit
class TestQaRailRegistry:
    def test_all_atoms_discovered(self):
        discover()  # idempotent
        for name in _ALL:
            assert get_atom_meta(name) is not None, f"{name} not registered"
            assert callable(get_atom_callable(name)), f"{name} has no callable"

    def test_rails_produce_qa_rail_reviews(self):
        discover()
        for name in _RAILS:
            assert "qa_rail_reviews" in get_atom_meta(name).produces
            assert "content" in get_atom_meta(name).requires

    def test_aggregate_contract(self):
        discover()
        m = get_atom_meta("qa.aggregate")
        assert "qa_rail_reviews" in m.requires
        assert "qa_final_score" in m.produces and "qa_final_verdict" in m.produces

    def test_fanout_spec_validates(self):
        """A graph that runs the rails then aggregate must pass the Plan-1
        requires/produces validator — the safety net Plan 4 relies on."""
        discover()
        spec = {
            "name": "qa_block",
            "entry": "critic",
            "nodes": [
                {"id": "critic", "atom": "qa.critic"},
                {"id": "deepeval", "atom": "qa.deepeval"},
                {"id": "guardrails", "atom": "qa.guardrails"},
                {"id": "ragas", "atom": "qa.ragas"},
                {"id": "vision", "atom": "qa.vision"},
                {"id": "aggregate", "atom": "qa.aggregate"},
            ],
            "edges": [
                {"from": "critic", "to": "deepeval"},
                {"from": "deepeval", "to": "guardrails"},
                {"from": "guardrails", "to": "ragas"},
                {"from": "ragas", "to": "vision"},
                {"from": "vision", "to": "aggregate"},
                {"from": "aggregate", "to": "END"},
            ],
        }
        ok, errors = pipeline_architect._validate_spec(spec)
        assert ok is True, errors
