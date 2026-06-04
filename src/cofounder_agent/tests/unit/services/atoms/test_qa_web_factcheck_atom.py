"""Unit tests for the qa.web_factcheck atom + the known_wrong_fact rescue
(Glad-Labs/poindexter#661).

Two things the #355 cutover dropped and this restores:

1. The ``web_factcheck`` rail itself — DuckDuckGo product/spec verification
   delegating to ``MultiModelQA._web_fact_check``.
2. THE REGRESSION: the ``known_wrong_fact`` web-rescue. On the live path,
   ``qa.programmatic`` emitted a non-advisory ``known_wrong_fact`` veto that
   HARD-REJECTED legit post-cutoff content with no web second opinion. The
   rescue (now in ``qa.aggregate`` via ``_qa_rail_common``) suppresses that
   validator veto when the web fact-check confirmed the claim — exactly what
   ``review()`` did.
"""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_web_factcheck
from modules.content.atoms._qa_rail_common import (
    aggregate_rail_reviews,
    known_wrong_fact_rescued,
)
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(**over):
    base = {
        "content": "The RTX 5090 ships with 32GB of VRAM.",
        "topic": "RTX 5090",
        "seo_title": "RTX 5090 deep dive",
        "site_config": _Cfg(),
    }
    base.update(over)
    return base


_ADVISORY_STATES = {"web_factcheck": (True, False)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


@pytest.mark.unit
class TestQaWebFactcheckAtom:
    def test_meta(self):
        m = qa_web_factcheck.ATOM_META
        assert m.name == "qa.web_factcheck"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        assert await qa_web_factcheck.run({"content": "", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_noops(self):
        assert await qa_web_factcheck.run({"content": "hi"}) == {}

    async def test_review_emitted(self, monkeypatch):
        async def fc(self, title, topic, content, existing):
            return ReviewerResult("web_factcheck", True, 100.0, "verified", "web_factcheck")
        monkeypatch.setattr(MultiModelQA, "_web_fact_check", fc)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_web_factcheck.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "web_factcheck"
        assert rev["provider"] == "web_factcheck"

    async def test_none_when_no_claims(self, monkeypatch):
        async def fc(self, title, topic, content, existing):
            return None
        monkeypatch.setattr(MultiModelQA, "_web_fact_check", fc)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        assert await qa_web_factcheck.run(_state()) == {}

    async def test_existing_reviews_passed_as_views(self, monkeypatch):
        """The upstream rail reviews are wrapped so _web_fact_check's
        ``r.provider`` / ``r.approved`` attribute access keeps working."""
        seen = {}

        async def fc(self, title, topic, content, existing):
            seen["existing"] = existing
            return ReviewerResult("web_factcheck", True, 80.0, "ok", "web_factcheck")
        monkeypatch.setattr(MultiModelQA, "_web_fact_check", fc)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        upstream = [
            {"reviewer": "ollama_qa", "approved": False, "score": 40.0,
             "provider": "ollama", "advisory": False},
        ]
        await qa_web_factcheck.run(_state(qa_rail_reviews=upstream))
        existing = seen["existing"]
        assert len(existing) == 1
        # Attribute access (not dict access) — _web_fact_check reads r.provider.
        assert existing[0].provider == "ollama"
        assert existing[0].approved is False
        assert existing[0].score == 40.0

    async def test_advisory_marks_review(self, monkeypatch):
        async def fc(self, title, topic, content, existing):
            return ReviewerResult("web_factcheck", False, 30.0, "weak", "web_factcheck")
        monkeypatch.setattr(MultiModelQA, "_web_fact_check", fc)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_web_factcheck.run(_state())
        rev = out["qa_rail_reviews"][0]
        # Advisory → does not veto even though it failed.
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "web_factcheck" not in decision["vetoed_by"]


def _validator_kwf_veto():
    """A non-advisory programmatic_validator review failing on a
    known_wrong_fact (score 0 = critical)."""
    return {
        "reviewer": "programmatic_validator", "approved": False, "score": 0.0,
        "provider": "programmatic", "advisory": False,
        "feedback": "1 critical issue(s) — first: known_wrong_fact: RTX 5090",
    }


def _web(approved: bool):
    return {
        "reviewer": "web_factcheck", "approved": approved, "score": 100.0 if approved else 0.0,
        "provider": "web_factcheck", "advisory": True,
        "feedback": "Web fact-check",
    }


@pytest.mark.unit
class TestKnownWrongFactRescue:
    """THE #661 regression fix: a stale-regex known_wrong_fact veto must be
    SUPPRESSED when the web fact-check confirmed the claim — restoring the
    deleted review()'s rescue so legit post-cutoff content stops being
    hard-rejected with no web second opinion."""

    def test_rescue_helper_suppresses_when_web_approved(self):
        reviews = [_validator_kwf_veto(), _web(True)]
        assert known_wrong_fact_rescued(
            reviews, ["programmatic_validator"], known_wrong_fact_only=True,
        ) is True

    def test_rescue_helper_holds_when_web_failed(self):
        reviews = [_validator_kwf_veto(), _web(False)]
        assert known_wrong_fact_rescued(
            reviews, ["programmatic_validator"], known_wrong_fact_only=True,
        ) is False

    def test_rescue_helper_holds_when_web_missing(self):
        reviews = [_validator_kwf_veto()]
        assert known_wrong_fact_rescued(
            reviews, ["programmatic_validator"], known_wrong_fact_only=True,
        ) is False

    def test_rescue_helper_needs_kwf_flag(self):
        """Without the known_wrong_fact-only flag (a normal fabrication), the
        web check NEVER rescues — only stale-regex known_wrong_fact qualifies."""
        reviews = [_validator_kwf_veto(), _web(True)]
        assert known_wrong_fact_rescued(
            reviews, ["programmatic_validator"], known_wrong_fact_only=False,
        ) is False

    def test_rescue_helper_holds_when_another_rail_also_vetoes(self):
        """If something OTHER than the validator also vetoes, the rescue does
        not apply — the post fails for the other reason."""
        reviews = [_validator_kwf_veto(), _web(True),
                   {"reviewer": "llm_critic", "approved": False, "score": 30.0,
                    "provider": "ollama", "advisory": False}]
        assert known_wrong_fact_rescued(
            reviews, ["programmatic_validator", "llm_critic"], known_wrong_fact_only=True,
        ) is False

    def test_aggregate_rescues_and_approves(self):
        """End-to-end through the aggregator: known_wrong_fact-only + web-approved
        → the validator veto is removed and the pass is APPROVED (no wrong
        hard-reject). This is the regression the #355 cutover introduced."""
        reviews = [_validator_kwf_veto(), _web(True),
                   {"reviewer": "llm_critic", "approved": True, "score": 90.0,
                    "provider": "ollama", "advisory": False}]
        decision = aggregate_rail_reviews(reviews, known_wrong_fact_only=True)
        assert decision["known_wrong_fact_rescued"] is True
        assert "programmatic_validator" not in decision["vetoed_by"]
        assert decision["approved"] is True
        assert decision["qa_final_verdict"] == "approve"

    def test_aggregate_upholds_reject_without_rescue(self):
        """Same veto, but web did NOT confirm → the validator veto STANDS, so
        the post is still rejected (the genuinely-wrong fact case)."""
        reviews = [_validator_kwf_veto(), _web(False)]
        decision = aggregate_rail_reviews(reviews, known_wrong_fact_only=True)
        assert decision["known_wrong_fact_rescued"] is False
        assert "programmatic_validator" in decision["vetoed_by"]
        assert decision["approved"] is False

    def test_aggregate_without_flag_keeps_old_behavior(self):
        """The flag defaults False → aggregation is unchanged for every normal
        pass (no rescue field interference)."""
        reviews = [{"reviewer": "ollama_qa", "approved": True, "score": 90.0,
                    "provider": "ollama", "advisory": False}]
        decision = aggregate_rail_reviews(reviews)
        assert decision["known_wrong_fact_rescued"] is False
        assert decision["approved"] is True
