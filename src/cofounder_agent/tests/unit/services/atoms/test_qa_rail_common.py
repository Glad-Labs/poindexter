"""Unit tests for the pure rail-aggregation helper (atom-cutover Plan 3, #355).
No DB, no mocks — exercises reviewer_to_dict + aggregate_rail_reviews."""

from __future__ import annotations

import pytest

from modules.content.atoms._qa_rail_common import aggregate_rail_reviews, reviewer_to_dict


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
