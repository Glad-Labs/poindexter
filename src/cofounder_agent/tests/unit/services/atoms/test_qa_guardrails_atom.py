"""Unit tests for the qa.guardrails atom (atom-cutover Plan 3, #355)."""

from __future__ import annotations

import pytest

from services.atoms import qa_guardrails
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {"content": "a sufficiently long blog body", "topic": "t", "site_config": _Cfg()}


@pytest.mark.unit
class TestQaGuardrailsAtom:
    def test_meta(self):
        m = qa_guardrails.ATOM_META
        assert m.name == "qa.guardrails"
        assert "qa_rail_reviews" in m.produces
        assert m.parallelizable is True

    async def test_collects_advisory(self, monkeypatch):
        async def brand(self, content):
            return ReviewerResult("guardrails_brand", True, 100.0, "ok", "programmatic")

        async def comp(self, content):
            return None  # no competitors configured

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        out = await qa_guardrails.run(_state())
        revs = out["qa_rail_reviews"]
        assert [r["reviewer"] for r in revs] == ["guardrails_brand"]
        assert revs[0]["advisory"] is True

    async def test_all_none(self, monkeypatch):
        async def brand(self, content):
            return None

        async def comp(self, content):
            return None

        monkeypatch.setattr(MultiModelQA, "_check_guardrails_brand", brand)
        monkeypatch.setattr(MultiModelQA, "_check_guardrails_competitor", comp)
        assert await qa_guardrails.run(_state()) == {}

    async def test_empty_content(self):
        assert await qa_guardrails.run({"content": "", "site_config": _Cfg()}) == {}
