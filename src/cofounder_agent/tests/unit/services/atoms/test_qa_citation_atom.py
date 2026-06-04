"""Unit tests for the qa.citations atom (Glad-Labs/poindexter#659).

Pins the contract that the dead-link / minimum-citation gate runs on the live
graph_def path again: the rail delegates to ``MultiModelQA._check_citations``,
appends a ``ReviewerResult`` (``provider='http_head'``) to ``qa_rail_reviews``,
and its advisory status is DB-driven via
``qa_gates.citation_verifier.required_to_pass`` (seeded advisory-first → scores
but does not veto; flip required to restore the dead-link hard veto).
"""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_citation
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(**over):
    base = {
        "content": "a blog body citing https://example.com/a and https://example.com/b",
        "topic": "citations",
        "site_config": _Cfg(),
    }
    base.update(over)
    return base


_ADVISORY_STATES = {"citation_verifier": (True, False)}
_HARD_GATE_STATES = {"citation_verifier": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


@pytest.mark.unit
class TestQaCitationAtom:
    def test_meta(self):
        m = qa_citation.ATOM_META
        assert m.name == "qa.citations"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        assert await qa_citation.run({"content": "", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_noops(self):
        assert await qa_citation.run({"content": "hello"}) == {}

    async def test_citation_review_emitted(self, monkeypatch):
        async def chk(self, content):
            return ReviewerResult("citation_verifier", True, 100.0, "all links alive", "http_head")
        monkeypatch.setattr(MultiModelQA, "_check_citations", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_citation.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "citation_verifier"
        assert rev["provider"] == "http_head"

    async def test_none_when_disabled_or_no_urls(self, monkeypatch):
        """_check_citations returns None when flagged off or no external URLs."""
        async def chk(self, content):
            return None
        monkeypatch.setattr(MultiModelQA, "_check_citations", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        assert await qa_citation.run(_state()) == {}

    async def test_advisory_first_dead_links_do_not_veto(self, monkeypatch):
        """Seeded advisory → a high dead-link ratio scores but does NOT veto."""
        from modules.content.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, content):
            return ReviewerResult("citation_verifier", False, 20.0, "60% dead", "http_head")
        monkeypatch.setattr(MultiModelQA, "_check_citations", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_citation.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "citation_verifier" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        """required_to_pass=true → the dead-link hard veto bites again."""
        from modules.content.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, content):
            return ReviewerResult("citation_verifier", False, 10.0, "all dead", "http_head")
        monkeypatch.setattr(MultiModelQA, "_check_citations", chk)
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_citation.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "citation_verifier" in decision["vetoed_by"]
