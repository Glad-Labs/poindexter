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


def _capture_findings(monkeypatch) -> list[dict]:
    """Collect emit_finding kwargs. The atom imports emit_finding inside the
    function body, so patching the module attribute intercepts it."""
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


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


@pytest.mark.unit
class TestNoMeasurement:
    """poindexter#875 — a rail that measured NOTHING must not report a score.

    evaluate() returns score=None on every skip path (no samples, embedding
    step down, caught error). Those used to return 1.0, which this atom
    recorded as a 100.0 self-consistency review — a check that never ran
    rendering as a perfect pass.
    """

    @staticmethod
    def _patch_no_measurement(monkeypatch, reason: str):
        async def mock_evaluate(*, content, topic, site_config):
            return (True, None, reason)
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )

    async def test_no_measurement_appends_no_review(self, monkeypatch):
        _capture_findings(monkeypatch)
        self._patch_no_measurement(
            monkeypatch, "self-consistency-skipped: embedding step failed",
        )
        out = await qa_self_consistency.run(_state())
        assert out == {}, "a rail that measured nothing must append no review"

    async def test_no_measurement_never_scores_100(self, monkeypatch):
        """The precise regression: the exact prod row was score=100.0 with
        feedback 'self-consistency-skipped: embedding step failed'."""
        _capture_findings(monkeypatch)
        self._patch_no_measurement(
            monkeypatch, "self-consistency-skipped: embedding step failed",
        )
        out = await qa_self_consistency.run(_state())
        reviews = out.get("qa_rail_reviews", [])
        assert not any(r.get("score") == 100.0 for r in reviews)

    async def test_no_measurement_does_not_inflate_aggregate(self, monkeypatch):
        """A skipped rail must be ABSENT from the aggregate, not a passing
        member of it.

        qa_rail_reviews is an accumulating channel across all 12 qa.* atoms, so
        this atom returning {} leaves the other rails' reviews untouched.

        Graduated (required_to_pass=true), the old fake 100.0 was measurably
        corrupting: it entered the provider-weighted mean and lifted a lone
        72.0 peer to 86.0 — +14 points of score from a check that never ran,
        enough to carry a sub-threshold post over the prod bar of 80. (While
        advisory the fake 100 was excluded from the mean, so the damage there
        was pass-rate inflation on the QA Rails dashboard rather than score
        movement — but advisory-forever is not the plan, and graduation is
        exactly when a QA rail is trusted most.)
        """
        _capture_findings(monkeypatch)
        self._patch_no_measurement(
            monkeypatch, "self-consistency-skipped: embedding step failed",
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_self_consistency.run(_state())

        peer = {
            "reviewer": "ollama_critic", "approved": True, "score": 72.0,
            "feedback": "ok", "provider": "ollama_critic",
        }
        channel = [peer, *out.get("qa_rail_reviews", [])]
        assert channel == [peer], "the skipped rail contributed no review"
        assert aggregate_rail_reviews(channel)["qa_final_score"] == 72.0

        # The exact regression, spelled out: this is what the old code built.
        stale = aggregate_rail_reviews([peer, {
            "reviewer": "self_consistency", "approved": True, "score": 100.0,
            "feedback": "self-consistency-skipped: embedding step failed",
            "provider": "self_consistency_gate",
        }])
        assert stale["qa_final_score"] == 86.0, (
            "guard on the arithmetic this test claims: a fake 100 lifts 72→86"
        )

    async def test_no_measurement_emits_warn_finding(self, monkeypatch):
        """The disappearance must be LOUD. severity=warn routes to Discord;
        info would be dashboard-only and the rail could go dark unnoticed."""
        calls = _capture_findings(monkeypatch)
        self._patch_no_measurement(
            monkeypatch, "self-consistency-skipped: embedding step failed",
        )
        await qa_self_consistency.run(_state())
        assert len(calls) == 1
        assert calls[0]["kind"] == "qa_rail_degraded"
        assert calls[0]["severity"] == "warn"
        assert calls[0]["dedup_key"] == "qa_rail_degraded:self_consistency"
        assert "embedding step failed" in calls[0]["body"]
        assert calls[0]["extra"]["rail"] == "self_consistency"

    async def test_no_measurement_still_never_vetoes(self, monkeypatch):
        """Even graduated to a hard gate (required_to_pass=true), a rail that
        could not run must not reject the post — it just isn't there. This is
        the fail-OPEN half of the contract, which the fix deliberately keeps.

        Graduation is the case that made the old 1.0 dangerous: with
        required_to_pass=true, every skip would have been a silent hard PASS.
        Absent-and-warned is the honest version of the same non-veto.
        """
        calls = _capture_findings(monkeypatch)
        self._patch_no_measurement(
            monkeypatch, "self-consistency-skipped: ollama unreachable",
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_self_consistency.run(_state())

        peer = {
            "reviewer": "ollama_critic", "approved": True, "score": 72.0,
            "feedback": "ok", "provider": "ollama_critic",
        }
        decision = aggregate_rail_reviews([peer, *out.get("qa_rail_reviews", [])])
        assert decision["approved"] is True
        assert "self_consistency" not in decision["vetoed_by"]
        assert len(calls) == 1, "silently passing open is the bug — it must warn"

    async def test_real_measurement_emits_no_finding(self, monkeypatch):
        """A rail that DID measure — even a failing one — is not a finding."""
        calls = _capture_findings(monkeypatch)

        async def mock_evaluate(*, content, topic, site_config):
            return (False, 0.31, "mean_similarity=0.31 < threshold=0.55")
        monkeypatch.setattr(
            "modules.content.atoms.qa_self_consistency._rail_evaluate",
            mock_evaluate,
        )
        out = await qa_self_consistency.run(_state())
        assert out["qa_rail_reviews"][0]["score"] == 31.0
        assert calls == []
