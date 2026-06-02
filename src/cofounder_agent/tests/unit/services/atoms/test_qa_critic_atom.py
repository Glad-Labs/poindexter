"""Unit tests for the qa.critic atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_critic
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t",
            "seo_title": "A Title", "research_context": None, "site_config": _Cfg()}


@pytest.mark.unit
class TestQaCriticAtom:
    def test_meta(self):
        m = qa_critic.ATOM_META
        assert m.name == "qa.critic"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_emits_non_advisory_review(self, monkeypatch):
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return ReviewerResult("ollama_qa", True, 84.0, "looks good", "ollama"), {"cost": 0.0}

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        out = await qa_critic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "ollama_qa"
        assert rev["advisory"] is False  # the critic is a hard gate

    async def test_none_result_yields_no_key(self, monkeypatch):
        async def critic(self, title, content, topic, model_override=None, research_sources=None):
            return None

        monkeypatch.setattr(MultiModelQA, "_review_with_cloud_model", critic)
        assert await qa_critic.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_critic.run({"content": "", "site_config": _Cfg()}) == {}
