"""Unit tests for the qa.deepeval atom (atom-cutover Plan 3, #355).
Monkeypatches MultiModelQA._check_* so no DeepEval/Ollama is invoked."""

from __future__ import annotations

import pytest

from services.atoms import qa_deepeval
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(content="a real blog body that is long enough"):
    return {"content": content, "topic": "widgets", "research_context": None, "site_config": _Cfg()}


@pytest.mark.unit
class TestQaDeepevalAtom:
    def test_meta(self):
        m = qa_deepeval.ATOM_META
        assert m.name == "qa.deepeval"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_non_none_rails_as_advisory(self, monkeypatch):
        def brand(self, content, topic):
            return ReviewerResult("deepeval_brand_fabrication", True, 95.0, "clean", "programmatic")

        async def geval(self, content, topic):
            return ReviewerResult("deepeval_g_eval", True, 82.0, "ok", "ollama")

        async def faith(self, content, research):
            return None  # no research → skipped

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)

        out = await qa_deepeval.run(_state())
        revs = out["qa_rail_reviews"]
        assert {r["reviewer"] for r in revs} == {"deepeval_brand_fabrication", "deepeval_g_eval"}
        assert all(r["advisory"] is True for r in revs)

    async def test_all_rails_none_yields_no_key(self, monkeypatch):
        def brand(self, content, topic):
            return None

        async def geval(self, content, topic):
            return None

        async def faith(self, content, research):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_deepeval_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_g_eval", geval)
        monkeypatch.setattr(MultiModelQA, "_check_deepeval_faithfulness", faith)
        out = await qa_deepeval.run(_state())
        assert out == {}

    async def test_empty_content_short_circuits(self):
        out = await qa_deepeval.run(_state(content="   "))
        assert out == {}

    async def test_no_site_config_short_circuits(self):
        out = await qa_deepeval.run({"content": "body", "topic": "t"})
        assert out == {}
