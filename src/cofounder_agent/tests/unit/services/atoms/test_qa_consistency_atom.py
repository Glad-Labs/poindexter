"""Unit tests for the qa.consistency atom (Glad-Labs/poindexter#660).

Pins the contract that the internal self-contradiction check runs on the live
graph_def path again: the rail delegates to
``MultiModelQA._check_internal_consistency``, appends a ``ReviewerResult``
(reviewer ``internal_consistency``, ``provider='consistency_gate'``) to
``qa_rail_reviews``, and its advisory status is DB-driven via the baseline
``qa_gates.consistency`` row (advisory → scores, never vetoes).
"""

from __future__ import annotations

import pytest

from services.atoms import qa_consistency
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(**over):
    base = {
        "content": "section 1 says no React; section 3 says use Next.js",
        "topic": "frameworks",
        "site_config": _Cfg(),
    }
    base.update(over)
    return base


# Baseline: consistency is enabled=true, required_to_pass=false (advisory).
_ADVISORY_STATES = {"consistency": (True, False)}
# Operator-graduated.
_HARD_GATE_STATES = {"consistency": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


@pytest.mark.unit
class TestQaConsistencyAtom:
    def test_meta(self):
        m = qa_consistency.ATOM_META
        assert m.name == "qa.consistency"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        assert await qa_consistency.run({"content": " ", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_noops(self):
        assert await qa_consistency.run({"content": "hello"}) == {}

    async def test_consistency_review_emitted(self, monkeypatch):
        async def chk(self, content):
            return ReviewerResult("internal_consistency", True, 95.0, "consistent", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_internal_consistency", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "internal_consistency"
        assert rev["provider"] == "consistency_gate"

    async def test_none_when_ollama_unavailable(self, monkeypatch):
        async def chk(self, content):
            return None
        monkeypatch.setattr(MultiModelQA, "_check_internal_consistency", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        assert await qa_consistency.run(_state()) == {}

    async def test_advisory_baseline_does_not_veto(self, monkeypatch):
        """consistency is advisory in the baseline → a contradiction scores but
        does NOT veto (the legacy low-score escape lived in review(), not here)."""
        from services.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, content):
            return ReviewerResult("internal_consistency", False, 15.0, "contradicts", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_internal_consistency", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "internal_consistency" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        from services.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, content):
            return ReviewerResult("internal_consistency", False, 15.0, "contradicts", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_internal_consistency", chk)
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "internal_consistency" in decision["vetoed_by"]
