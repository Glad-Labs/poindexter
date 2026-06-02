"""Unit tests for the qa.ragas atom (atom-cutover Plan 3, #355).
Advisory is now DB-driven via qa_gates.required_to_pass (poindexter#454)."""

from __future__ import annotations

import pytest

from services.atoms import qa_ragas
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(research="some retrieved context paragraphs"):
    return {"content": "a sufficiently long blog body", "topic": "t",
            "research_context": research, "site_config": _Cfg()}


_ADVISORY_STATES = {"ragas_eval": (True, False)}
_GRADUATED_STATES = {"ragas_eval": (True, True)}


@pytest.mark.unit
class TestQaRagasAtom:
    def test_meta(self):
        m = qa_ragas.ATOM_META
        assert m.name == "qa.ragas"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_advisory_when_gate_required_to_pass_false(self, monkeypatch):
        """DB says required_to_pass=False → advisory=True (baseline prod behavior)."""
        async def ragas(self, content, topic, research):
            return ReviewerResult("ragas_eval", False, 55.0, "low", "programmatic")

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_ragas.run(_state())
        assert out["qa_rail_reviews"][0]["reviewer"] == "ragas_eval"
        assert out["qa_rail_reviews"][0]["advisory"] is True

    async def test_required_when_gate_graduated(self, monkeypatch):
        """When ragas_eval is graduated (required_to_pass=True), advisory must be False."""
        async def ragas(self, content, topic, research):
            return ReviewerResult("ragas_eval", True, 77.0, "ok", "programmatic")

        async def fake_gate_states(self):
            return _GRADUATED_STATES

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_ragas.run(_state())
        assert out["qa_rail_reviews"][0]["advisory"] is False

    async def test_no_gate_states_leaves_review_required(self, monkeypatch):
        """Empty gate_states (no DB) → review stays required (advisory=False)."""
        async def ragas(self, content, topic, research):
            return ReviewerResult("ragas_eval", True, 77.0, "ok", "programmatic")

        async def fake_gate_states(self):
            return {}

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_ragas.run(_state())
        assert out["qa_rail_reviews"][0]["advisory"] is False

    async def test_none_when_no_research(self, monkeypatch):
        async def ragas(self, content, topic, research):
            return None

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        assert await qa_ragas.run(_state(research=None)) == {}

    async def test_empty_content(self):
        assert await qa_ragas.run({"content": "  ", "site_config": _Cfg()}) == {}
