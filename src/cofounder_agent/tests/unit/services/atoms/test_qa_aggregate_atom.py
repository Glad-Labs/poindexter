"""Unit tests for the qa.aggregate atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_aggregate


class _Cfg:
    def __init__(self, vals=None):
        self._vals = vals or {}

    def get(self, key, default=None):
        return self._vals.get(key, default)


@pytest.mark.unit
class TestQaAggregateAtom:
    def test_meta(self):
        m = qa_aggregate.ATOM_META
        assert m.name == "qa.aggregate"
        assert "qa_rail_reviews" in m.requires
        assert "qa_final_score" in m.produces
        assert "qa_final_verdict" in m.produces

    async def test_approve_path(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 90.0
        assert "_halt" not in out

    async def test_reject_halts(self):
        state = {
            "site_config": _Cfg(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"
        assert out["_halt"] is True
        assert "ollama_qa" in out["_halt_reason"]

    async def test_reads_threshold_from_site_config(self):
        # Threshold 95 → an 90-scoring all-pass run now REJECTS.
        state = {
            "site_config": _Cfg({"qa_final_score_threshold": "95"}),
            "qa_rail_reviews": [
                {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            ],
        }
        out = await qa_aggregate.run(state)
        assert out["qa_final_verdict"] == "reject"

    async def test_missing_reviews_key_rejects_at_zero(self):
        out = await qa_aggregate.run({"site_config": _Cfg()})
        assert out["qa_final_score"] == 0.0
        assert out["_halt"] is True
