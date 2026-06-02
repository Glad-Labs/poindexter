"""Unit tests for the qa.guardrails atom (atom-cutover Plan 3, #355).
Advisory is now DB-driven via qa_gates.required_to_pass (poindexter#454)."""

from __future__ import annotations

import pytest

from services.atoms import qa_guardrails
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t", "site_config": _Cfg()}


_ADVISORY_STATES = {
    "guardrails_brand": (True, False),
    "guardrails_competitor": (True, False),
}

_GRADUATED_STATES = {
    "guardrails_brand": (True, True),  # graduated — now a hard gate
    "guardrails_competitor": (True, False),
}


@pytest.mark.unit
class TestQaGuardrailsAtom:
    def test_meta(self):
        m = qa_guardrails.ATOM_META
        assert m.name == "qa.guardrails"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_advisory_when_gate_required_to_pass_false(self, monkeypatch):
        """DB says required_to_pass=False → advisory=True (baseline prod behavior)."""
        async def brand(self, content):
            return ReviewerResult("guardrails_brand", False, 60.0, "issue", "programmatic")

        async def comp(self, content):
            return None  # no competitors configured

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_guardrails.run(_state())
        revs = out["qa_rail_reviews"]
        assert [r["reviewer"] for r in revs] == ["guardrails_brand"]
        assert revs[0]["advisory"] is True

    async def test_required_when_gate_graduated(self, monkeypatch):
        """When guardrails_brand is graduated (required_to_pass=True), advisory must be False."""
        async def brand(self, content):
            return ReviewerResult("guardrails_brand", False, 60.0, "issue", "programmatic")

        async def comp(self, content):
            return ReviewerResult("guardrails_competitor", True, 100.0, "ok", "programmatic")

        async def fake_gate_states(self):
            return _GRADUATED_STATES

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_guardrails.run(_state())
        revs = {r["reviewer"]: r for r in out["qa_rail_reviews"]}
        # brand graduated → required (advisory=False)
        assert revs["guardrails_brand"]["advisory"] is False
        # competitor still advisory
        assert revs["guardrails_competitor"]["advisory"] is True

    async def test_no_gate_states_leaves_reviews_required(self, monkeypatch):
        """Empty gate_states (no DB) → reviews stay required (advisory=False)."""
        async def brand(self, content):
            return ReviewerResult("guardrails_brand", True, 100.0, "ok", "programmatic")

        async def comp(self, content):
            return None

        async def fake_gate_states(self):
            return {}

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        out = await qa_guardrails.run(_state())
        assert out["qa_rail_reviews"][0]["advisory"] is False

    async def test_all_none(self, monkeypatch):
        async def brand(self, content):
            return None

        async def comp(self, content):
            return None

        async def fake_gate_states(self):
            return _ADVISORY_STATES

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)
        assert await qa_guardrails.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_guardrails.run({"content": "", "site_config": _Cfg()}) == {}
