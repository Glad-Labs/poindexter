"""Unit tests for the qa.topic_delivery atom (Glad-Labs/poindexter#658).

Pins the contract that the bait-and-switch topic-delivery gate runs on the live
graph_def path again: the rail delegates to
``MultiModelQA._check_topic_delivery``, appends a ``ReviewerResult`` to
``qa_rail_reviews``, and its advisory status is DB-driven via
``qa_gates.topic_delivery.required_to_pass`` (seeded advisory-first → scores but
does not veto; flip required to restore the legacy binary veto).
"""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_topic_delivery
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def get(self, key, default=None):
        return default


def _state(**over):
    base = {
        "content": "a sufficiently long blog body listing real indie hackers",
        "topic": "11 indie hackers to follow",
        "site_config": _Cfg(),
    }
    base.update(over)
    return base


# Advisory-first restore: topic_delivery seeded required_to_pass=false.
_ADVISORY_STATES = {"topic_delivery": (True, False)}
# Operator-graduated: required_to_pass=true → legacy binary veto restored.
_HARD_GATE_STATES = {"topic_delivery": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


@pytest.mark.unit
class TestQaTopicDeliveryAtom:
    def test_meta(self):
        m = qa_topic_delivery.ATOM_META
        assert m.name == "qa.topic_delivery"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        assert await qa_topic_delivery.run({"content": "  ", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_noops(self):
        assert await qa_topic_delivery.run({"content": "hello world"}) == {}

    async def test_empty_topic_noops(self, monkeypatch):
        """No topic → nothing to check delivery against (legacy no-op)."""
        async def boom(self, topic, content):  # pragma: no cover
            raise AssertionError("_check_topic_delivery ran with empty topic")
        monkeypatch.setattr(MultiModelQA, "_check_topic_delivery", boom)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        assert await qa_topic_delivery.run(_state(topic="  ")) == {}

    async def test_delivery_review_emitted(self, monkeypatch):
        async def chk(self, topic, content):
            return ReviewerResult("topic_delivery", True, 90.0, "delivers", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_topic_delivery", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_topic_delivery.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "topic_delivery"
        assert rev["provider"] == "consistency_gate"

    async def test_none_when_ollama_unavailable(self, monkeypatch):
        async def chk(self, topic, content):
            return None
        monkeypatch.setattr(MultiModelQA, "_check_topic_delivery", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        assert await qa_topic_delivery.run(_state()) == {}

    async def test_advisory_first_does_not_veto(self, monkeypatch):
        """Seeded advisory → a failing bait-and-switch scores but does NOT veto."""
        from modules.content.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, topic, content):
            return ReviewerResult("topic_delivery", False, 20.0, "no hackers named", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_topic_delivery", chk)
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_topic_delivery.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "topic_delivery" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        """required_to_pass=true → the legacy binary bait-and-switch veto bites."""
        from modules.content.atoms._qa_rail_common import aggregate_rail_reviews

        async def chk(self, topic, content):
            return ReviewerResult("topic_delivery", False, 10.0, "no hackers named", "consistency_gate")
        monkeypatch.setattr(MultiModelQA, "_check_topic_delivery", chk)
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_topic_delivery.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "topic_delivery" in decision["vetoed_by"]
        assert decision["approved"] is False
