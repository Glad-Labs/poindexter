"""Unit tests for the pure rail-aggregation helper (atom-cutover Plan 3, #355).
No DB, no mocks — exercises reviewer_to_dict + aggregate_rail_reviews."""

from __future__ import annotations

import pytest

from modules.content.atoms._qa_rail_common import (
    aggregate_rail_reviews,
    missing_required_gates,
    reviewer_to_dict,
)


class _R:
    def __init__(self, reviewer, approved, score, provider, advisory=False, feedback="fb"):
        self.reviewer = reviewer
        self.approved = approved
        self.score = score
        self.provider = provider
        self.advisory = advisory
        self.feedback = feedback


@pytest.mark.unit
class TestReviewerToDict:
    def test_serializes_all_fields(self):
        d = reviewer_to_dict(_R("ollama_qa", True, 88.0, "ollama", advisory=False))
        assert d == {
            "reviewer": "ollama_qa", "approved": True, "score": 88.0,
            "feedback": "fb", "provider": "ollama", "advisory": False,
        }


@pytest.mark.unit
class TestAggregate:
    def test_all_pass_above_threshold_approves(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            {"reviewer": "deepeval_g_eval", "approved": True, "score": 80.0, "provider": "ollama", "advisory": True},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is True
        assert out["qa_final_verdict"] == "approve"
        assert out["qa_final_score"] == 85.0  # equal weights (both ollama=0.6) → mean
        assert out["vetoed_by"] == []

    def test_nonadvisory_failure_vetoes(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": False, "score": 95.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is False
        assert out["qa_final_verdict"] == "reject"
        assert out["vetoed_by"] == ["ollama_qa"]

    def test_advisory_failure_does_not_veto(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 90.0, "provider": "ollama", "advisory": False},
            {"reviewer": "guardrails_brand", "approved": False, "score": 0.0, "provider": "programmatic", "advisory": True},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        # advisory fail doesn't veto; score 0 is ignored (score > 0 filter) → final = 90
        assert out["approved"] is True
        assert out["vetoed_by"] == []
        assert out["qa_final_score"] == 90.0

    def test_below_threshold_rejects_even_if_all_pass(self):
        reviews = [
            {"reviewer": "ollama_qa", "approved": True, "score": 60.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["approved"] is False
        assert out["qa_final_verdict"] == "reject"

    def test_empty_reviews_rejects_at_zero(self):
        out = aggregate_rail_reviews([], validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=70.0)
        assert out["qa_final_score"] == 0.0
        assert out["approved"] is False

    def test_provider_weights_applied(self):
        # programmatic weight 0.4, ollama weight 0.6 → weighted mean of (100, 50)
        reviews = [
            {"reviewer": "validator", "approved": True, "score": 100.0, "provider": "programmatic", "advisory": False},
            {"reviewer": "ollama_qa", "approved": True, "score": 50.0, "provider": "ollama", "advisory": False},
        ]
        out = aggregate_rail_reviews(reviews, validator_weight=0.4, critic_weight=0.6, gate_weight=0.3, threshold=10.0)
        # (100*0.4 + 50*0.6) / (0.4+0.6) = (40+30)/1.0 = 70.0
        assert out["qa_final_score"] == 70.0


@pytest.mark.unit
class TestMissingRequiredGates:
    """Vacuous-pass guard helper (poindexter#680) — alias-aware presence.

    Regression cover for the prod incident where every otherwise-passing
    post was hard-rejected: the critic writes ``reviewer="ollama_critic"``
    but its required gate row is named ``llm_critic``. The guard's raw
    name membership test saw ``llm_critic`` as absent and failed closed.
    """

    # (enabled, required_to_pass) — the shape _load_gate_states returns.
    _PROD_REQUIRED = {
        "programmatic_validator": (True, True),
        "llm_critic": (True, True),
        "deepeval_brand_fabrication": (True, True),
        "deepeval_faithfulness": (True, True),
        "citation_verifier": (True, False),  # enabled, advisory
        "web_factcheck": (False, True),       # required but disabled
    }

    def _passing_reviews(self):
        """The reviewer names a fully-passing prod QA run actually emits."""
        return [
            {"reviewer": "programmatic_validator"},
            {"reviewer": "ollama_critic"},  # ← aliases to llm_critic
            {"reviewer": "deepeval_brand_fabrication"},
            {"reviewer": "deepeval_faithfulness"},
            {"reviewer": "citation_verifier"},
        ]

    def test_ollama_critic_satisfies_required_llm_critic_gate(self):
        # The core regression: ollama_critic must resolve to llm_critic so
        # the required gate is seen as present and nothing is "missing".
        assert missing_required_gates(self._passing_reviews(), self._PROD_REQUIRED) == []

    def test_internal_consistency_aliases_to_consistency(self):
        gate_states = {"consistency": (True, True)}
        reviews = [{"reviewer": "internal_consistency"}]
        assert missing_required_gates(reviews, gate_states) == []

    def test_identity_when_reviewer_equals_gate_name(self):
        gate_states = {"programmatic_validator": (True, True)}
        reviews = [{"reviewer": "programmatic_validator"}]
        assert missing_required_gates(reviews, gate_states) == []

    def test_genuinely_absent_required_gate_is_reported(self):
        # The guard's real job: a required+enabled rail that emitted no
        # review at all must still be caught (fail closed).
        reviews = [{"reviewer": "programmatic_validator"}]  # no critic ran
        gate_states = {
            "programmatic_validator": (True, True),
            "llm_critic": (True, True),
        }
        assert missing_required_gates(reviews, gate_states) == ["llm_critic"]

    def test_advisory_gate_absence_is_ignored(self):
        gate_states = {"citation_verifier": (True, False)}
        assert missing_required_gates([], gate_states) == []

    def test_disabled_required_gate_absence_is_ignored(self):
        gate_states = {"web_factcheck": (False, True)}
        assert missing_required_gates([], gate_states) == []

    def test_empty_gate_states_reports_nothing(self):
        # No-DB / fresh-checkout fallback: resolve_gate_states returns {}.
        assert missing_required_gates(self._passing_reviews(), {}) == []
