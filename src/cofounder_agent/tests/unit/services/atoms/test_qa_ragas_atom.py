"""Unit tests for the qa.ragas atom (atom-cutover Plan 3, #355)."""

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


@pytest.mark.unit
class TestQaRagasAtom:
    def test_meta(self):
        m = qa_ragas.ATOM_META
        assert m.name == "qa.ragas"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_advisory(self, monkeypatch):
        async def ragas(self, content, topic, research):
            return ReviewerResult("ragas_eval", True, 77.0, "ok", "programmatic")

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        out = await qa_ragas.run(_state())
        assert out["qa_rail_reviews"][0]["reviewer"] == "ragas_eval"
        assert out["qa_rail_reviews"][0]["advisory"] is True

    async def test_none_when_no_research(self, monkeypatch):
        async def ragas(self, content, topic, research):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_ragas_eval", ragas)
        assert await qa_ragas.run(_state(research=None)) == {}

    async def test_empty_content(self):
        assert await qa_ragas.run({"content": "  ", "site_config": _Cfg()}) == {}
