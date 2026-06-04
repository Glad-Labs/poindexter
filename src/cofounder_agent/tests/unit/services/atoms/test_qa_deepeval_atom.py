"""Unit tests for the qa.deepeval atom (atom-cutover Plan 3, #355).
Monkeypatches MultiModelQA._check_* so no DeepEval/Ollama is invoked.
Advisory is now DB-driven via qa_gates.required_to_pass (poindexter#454)."""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_deepeval
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(content="a real blog body that is long enough"):
    return {"content": content, "topic": "widgets", "research_context": None, "site_config": _Cfg()}


# Realistic gate states matching the baseline seed (all OSS rails advisory).
_ADVISORY_STATES = {
    "deepeval_brand_fabrication": (True, False),
    "deepeval_g_eval": (True, False),
    "deepeval_faithfulness": (True, False),
}

# Gate states simulating graduation: deepeval_g_eval promoted to required.
_GRADUATED_STATES = {
    "deepeval_brand_fabrication": (True, False),
    "deepeval_g_eval": (True, True),  # graduated — now a hard gate
    "deepeval_faithfulness": (True, False),
}


@pytest.mark.unit
class TestQaDeepevalAtom:
    def test_meta(self):
        m = qa_deepeval.ATOM_META
        assert m.name == "qa.deepeval"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_advisory_when_gate_required_to_pass_false(self, monkeypatch):
        """DB says required_to_pass=False → advisory=True (baseline prod behavior)."""
        def brand(self, content, topic):
            return ReviewerResult("deepeval_brand_fabrication", True, 95.0, "clean", "programmatic")

        async def geval(self, content, topic):
            return ReviewerResult("deepeval_g_eval", False, 55.0, "bad", "ollama")

        async def faith(self, content, research):
            return None  # no research → skipped

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)

        out = await qa_deepeval.run(_state())
        revs = out["qa_rail_reviews"]
        assert {r["reviewer"] for r in revs} == {"deepeval_brand_fabrication", "deepeval_g_eval"}
        assert all(r["advisory"] is True for r in revs)

    async def test_required_when_gate_graduated(self, monkeypatch):
        """When deepeval_g_eval is graduated (required_to_pass=True), advisory must be False."""
        def brand(self, content, topic):
            return ReviewerResult("deepeval_brand_fabrication", True, 95.0, "clean", "programmatic")

        async def geval(self, content, topic):
            return ReviewerResult("deepeval_g_eval", False, 55.0, "bad", "ollama")

        async def faith(self, content, research):
            return None

        async def fake_gate_states(self):
            return _GRADUATED_STATES

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)

        out = await qa_deepeval.run(_state())
        revs = {r["reviewer"]: r for r in out["qa_rail_reviews"]}
        # brand still advisory (required_to_pass=False)
        assert revs["deepeval_brand_fabrication"]["advisory"] is True
        # g_eval graduated → required (advisory=False)
        assert revs["deepeval_g_eval"]["advisory"] is False

    async def test_no_gate_states_leaves_reviews_required(self, monkeypatch):
        """Empty gate_states (no DB) → _mark_advisory_if_configured is a no-op →
        reviews stay required (advisory=False), matching legacy behavior."""
        def brand(self, content, topic):
            return ReviewerResult("deepeval_brand_fabrication", True, 95.0, "clean", "programmatic")

        async def geval(self, content, topic):
            return ReviewerResult("deepeval_g_eval", True, 82.0, "ok", "ollama")

        async def faith(self, content, research):
            return None

        async def fake_gate_states(self):
            return {}

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)

        out = await qa_deepeval.run(_state())
        revs = out["qa_rail_reviews"]
        assert {r["reviewer"] for r in revs} == {"deepeval_brand_fabrication", "deepeval_g_eval"}
        # no gate states → _mark_advisory_if_configured is a no-op → advisory stays False
        assert all(r["advisory"] is False for r in revs)

    async def test_all_rails_none_yields_no_key(self, monkeypatch):
        def brand(self, content, topic):
            return None

        async def geval(self, content, topic):
            return None

        async def faith(self, content, research):
            return None

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_deepeval.run(_state())
        assert out == {}

    async def test_empty_content_short_circuits(self):
        out = await qa_deepeval.run(_state(content="   "))
        assert out == {}

    async def test_no_site_config_short_circuits(self):
        out = await qa_deepeval.run({"content": "body", "topic": "t"})
        assert out == {}
