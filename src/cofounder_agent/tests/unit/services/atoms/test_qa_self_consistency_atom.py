"""Unit tests for qa.self_consistency atom (glad-labs-stack#621).

Pins the atom contract: delegates to self_consistency_rail.evaluate(),
appends a ReviewerResult (reviewer='self_consistency', provider='self_consistency_gate')
to qa_rail_reviews, and is advisory-first (DB-gated).
"""

from __future__ import annotations

import pytest

# Import will fail until the atom file is created.
from modules.content.atoms import qa_self_consistency
from modules.content.atoms._qa_rail_common import aggregate_rail_reviews
from modules.content.multi_model_qa import MultiModelQA


# Advisory-first restore: self_consistency seeded required_to_pass=false.
_ADVISORY_STATES = {"self_consistency": (True, False)}
# Operator-graduated: required_to_pass=true → hard veto.
_HARD_GATE_STATES = {"self_consistency": (True, True)}


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


class _Cfg:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    def get(self, key, default=None):
        if key == "self_consistency_enabled":
            return "true" if self._enabled else "false"
        return default

    def get_bool(self, key, default=False):
        if key == "self_consistency_enabled":
            return self._enabled
        return default


def _state(**over):
    base = {
        "content": "This is a test article about Python async patterns.",
        "topic": "Python async",
        "site_config": _Cfg(enabled=True),
    }
    base.update(over)
    return base


@pytest.mark.unit
class TestQaSelfConsistencyAtom:
    def test_meta(self):
        m = qa_self_consistency.ATOM_META
        assert m.name == "qa.self_consistency"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_empty_content_noops(self):
        result = await qa_self_consistency.run({"content": " ", "site_config": _Cfg()})
        assert result == {}

    async def test_missing_site_config_noops(self):
        result = await qa_self_consistency.run({"content": "hello"})
        assert result == {}

    async def test_rail_disabled_noops(self):
        """When self_consistency_enabled=false the atom is a no-op."""
        result = await qa_self_consistency.run(_state(site_config=_Cfg(enabled=False)))
        assert result == {}

    async def test_review_emitted_on_pass(self, monkeypatch):
        """A passing rail emits a ReviewerResult with approved=True."""
        async def mock_evaluate(*, content, topic, site_config):
            return (True, 0.82, "mean_similarity=0.82 >= threshold=0.55")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        out = await qa_self_consistency.run(_state())
        assert "qa_rail_reviews" in out
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "self_consistency"
        assert rev["approved"] is True
        assert rev["provider"] == "self_consistency_gate"

    async def test_review_emitted_on_fail(self, monkeypatch):
        """A failing rail emits a ReviewerResult with approved=False."""
        async def mock_evaluate(*, content, topic, site_config):
            return (False, 0.31, "mean_similarity=0.31 < threshold=0.55")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        out = await qa_self_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is False

    async def test_rail_exception_is_safe(self, monkeypatch):
        """An exception in evaluate() must NOT propagate — returns {} so the
        pipeline continues (self_consistency_rail.evaluate never raises, but
        test the atom's own guard layer)."""
        async def mock_evaluate(*, content, topic, site_config):
            raise RuntimeError("Ollama died")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        # Should not raise
        result = await qa_self_consistency.run(_state())
        assert result == {}

    async def test_advisory_first_does_not_veto(self, monkeypatch):
        """Seeded advisory (required_to_pass=false) → a FAILING self-consistency
        run scores but must NOT veto the pass.

        Regression for the advisory-veto bug: the atom skipped
        _mark_advisory_if_configured (assuming qa.aggregate read the gate
        row — it doesn't), so a failing run hard-rejected a post despite the
        gate being configured advisory in qa_gates."""
        async def mock_evaluate(*, content, topic, site_config):
            return (False, 0.31, "mean_similarity=0.31 < threshold=0.55")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_self_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "self_consistency" not in decision["vetoed_by"]

    async def test_graduated_to_hard_veto(self, monkeypatch):
        """required_to_pass=true → the gate becomes a real veto on a failing run."""
        async def mock_evaluate(*, content, topic, site_config):
            return (False, 0.20, "mean_similarity=0.20 < threshold=0.55")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_self_consistency.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "self_consistency" in decision["vetoed_by"]
        assert decision["approved"] is False
