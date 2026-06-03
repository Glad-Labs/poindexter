"""Unit tests for the qa.programmatic atom.

The programmatic ContentValidator (regex/heuristics, NO LLM) re-wired as a QA
rail. Restores the hard anti-hallucination gate that stopped running on the
live path when #355 cut over to the graph_def (the cutover ported qa.critic
but not the programmatic_validator / url_verifier legs the legacy
MultiModelQA.review() ran first).

Mirrors test_qa_critic_atom: advisory status is DB-driven via
qa_gates.programmatic_validator.required_to_pass — True in prod → a critical
fabrication is a real veto in qa.aggregate; False → advisory (operator lever).
"""

from __future__ import annotations

import pytest

from services.atoms import qa_programmatic
from services.content_validator import ValidationIssue, ValidationResult
from services.multi_model_qa import MultiModelQA


class _Cfg:
    def get(self, key, default=None):
        return default


def _state():
    return {
        "content": "a sufficiently long blog body about widgets and gears",
        "topic": "widgets",
        "seo_title": "A Title",
        "site_config": _Cfg(),
    }


# Prod baseline: programmatic_validator is a hard gate (required_to_pass=True).
_HARD_GATE_STATES = {"programmatic_validator": (True, True)}
# Operator-demoted: made advisory (required_to_pass=False).
_ADVISORY_STATES = {"programmatic_validator": (True, False)}


def _clean_result() -> ValidationResult:
    return ValidationResult(passed=True, issues=[])


def _fabrication_result() -> ValidationResult:
    return ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                severity="critical",
                category="fake_person",
                description="Fabricated person detected: 'Dr. Jane Q. Fake'",
                matched_text="Dr. Jane Q. Fake",
            )
        ],
    )


def _warning_result() -> ValidationResult:
    return ValidationResult(
        passed=True,
        issues=[
            ValidationIssue(
                severity="warning",
                category="code_block_density",
                description="lots of code",
                matched_text="```",
            )
        ],
    )


def _known_wrong_fact_result() -> ValidationResult:
    """A critical rejection whose ONLY critical is a known_wrong_fact — the
    stale-regex false-positive on a real post-cutoff product (#661)."""
    return ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                severity="critical",
                category="known_wrong_fact",
                description="known_wrong_fact: RTX 5090 has 32GB VRAM",
                matched_text="RTX 5090",
            )
        ],
    )


def _patch_gates(monkeypatch, states):
    async def gates(self):
        return states

    monkeypatch.setattr(MultiModelQA, "_load_gate_states", gates)


@pytest.mark.unit
class TestQaProgrammaticAtom:
    def test_meta(self):
        m = qa_programmatic.ATOM_META
        assert m.name == "qa.programmatic"
        assert "content" in m.requires
        assert "qa_rail_reviews" in m.produces

    async def test_critical_fabrication_vetoes_as_hard_gate(self, monkeypatch):
        """Prod baseline: required_to_pass=True → a critical fabrication yields a
        NON-advisory failing review, so qa.aggregate vetoes + rejects."""
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["reviewer"] == "programmatic_validator"
        assert rev["provider"] == "programmatic"
        assert rev["approved"] is False
        assert rev["advisory"] is False  # hard gate → real veto

    async def test_clean_content_passes(self, monkeypatch):
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _clean_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is True
        assert rev["score"] == 100.0

    async def test_warnings_shave_score_without_vetoing(self, monkeypatch):
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _warning_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is True  # warnings never veto
        assert rev["score"] < 100.0

    async def test_advisory_when_operator_demotes(self, monkeypatch):
        """required_to_pass=False → advisory=True (no veto), poindexter#454 lever."""
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is True

    async def test_no_gate_states_stays_required(self, monkeypatch):
        """Empty gate_states (no DB) → stays required (advisory=False) — fail-closed,
        matching the legacy 'every gate required' default."""
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, {})
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["advisory"] is False

    async def test_validator_crash_fails_closed(self, monkeypatch):
        """A validator exception must NOT pass silently — emit a failing review so
        a crashed anti-hallucination net rejects rather than waves content through."""

        def boom(**kw):
            raise RuntimeError("regex exploded")

        monkeypatch.setattr("services.content_validator.validate_content", boom)
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        rev = out["qa_rail_reviews"][0]
        assert rev["approved"] is False
        assert rev["advisory"] is False

    async def test_empty_content_yields_no_key(self):
        assert await qa_programmatic.run({"content": "", "site_config": _Cfg()}) == {}

    async def test_missing_site_config_yields_no_key(self):
        assert await qa_programmatic.run({"content": "hello world"}) == {}

    async def test_fabrication_rejects_through_aggregation(self, monkeypatch):
        """End-to-end proof of the C1 fix: a critical fabrication flowing from
        qa.programmatic into the real aggregation produces a REJECT — the gate
        that the #355 cutover silently dropped now bites again."""
        from services.atoms._qa_rail_common import aggregate_rail_reviews

        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert decision["approved"] is False
        assert "programmatic_validator" in decision["vetoed_by"]

    async def test_advisory_fabrication_does_not_veto_through_aggregation(self, monkeypatch):
        """When the operator demotes the gate to advisory, the same fabrication
        no longer vetoes (score still factored, but no reject)."""
        from services.atoms._qa_rail_common import aggregate_rail_reviews

        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, _ADVISORY_STATES)
        out = await qa_programmatic.run(_state())
        decision = aggregate_rail_reviews(out["qa_rail_reviews"])
        assert "programmatic_validator" not in decision["vetoed_by"]

    async def test_known_wrong_fact_only_sets_rescue_flag(self, monkeypatch):
        """#661: a known_wrong_fact-only critical sets qa_known_wrong_fact_only on
        state so qa.aggregate can apply the web fact-check rescue."""
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _known_wrong_fact_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        assert out.get("qa_known_wrong_fact_only") is True
        # The review still fails (the rescue happens in qa.aggregate, not here).
        assert out["qa_rail_reviews"][0]["approved"] is False

    async def test_non_kwf_fabrication_does_not_set_flag(self, monkeypatch):
        """A normal fabrication (fake_person) must NOT set the rescue flag —
        only stale-regex known_wrong_fact qualifies for the web rescue."""
        monkeypatch.setattr(
            "services.content_validator.validate_content", lambda **kw: _fabrication_result()
        )
        _patch_gates(monkeypatch, _HARD_GATE_STATES)
        out = await qa_programmatic.run(_state())
        assert "qa_known_wrong_fact_only" not in out
