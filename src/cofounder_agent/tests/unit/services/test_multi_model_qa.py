"""
Unit tests for services/multi_model_qa.py

Tests MultiModelQA review pipeline: programmatic validation gate,
cloud reviewer integration, weighted score aggregation, and graceful
handling when Ollama is unavailable.
All external calls (OllamaClient, Gemini) are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.content_validator import ValidationIssue, ValidationResult
from services.multi_model_qa import MultiModelQA, MultiModelResult, ReviewerResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GOOD_TITLE = "How to Deploy FastAPI on Railway"
GOOD_CONTENT = (
    "Railway makes it easy to deploy Python web applications. "
    "In this guide we walk through the steps to get a FastAPI "
    "project running in production with zero-downtime deploys."
)
GOOD_TOPIC = "deployment"

BAD_TITLE = "Glad Labs Revenue Growth"
BAD_CONTENT = (
    'According to a 2024 study by McKinsey, our revenue is growing. '
    '"This is amazing," said Sarah Johnson, CEO of Glad Labs. '
    "Glad Labs has been spending years perfecting this."
)


def _passing_validation() -> ValidationResult:
    return ValidationResult(passed=True, issues=[], score_penalty=0)


def _failing_validation() -> ValidationResult:
    return ValidationResult(
        passed=False,
        issues=[
            ValidationIssue(
                severity="critical",
                category="fake_person",
                description="Fabricated person: Sarah Johnson, CEO",
                matched_text="Sarah Johnson, CEO of Glad Labs",
            )
        ],
        score_penalty=40,
    )


def _mock_ollama_client(approved: bool = True, score: float = 85.0):
    """Return a mock OllamaClient that returns a valid JSON review."""
    import json

    client = AsyncMock()
    client.check_health = AsyncMock(return_value=True)
    client.generate = AsyncMock(return_value={
        "text": json.dumps({
            "approved": approved,
            "quality_score": score,
            "feedback": "Well-written and informative content.",
        }),
        "tokens": 50,
        "prompt_tokens": 200,
    })
    client.close = AsyncMock()
    return client


def _mock_ollama_client_down():
    """Return a mock OllamaClient that reports unhealthy."""
    client = AsyncMock()
    client.check_health = AsyncMock(return_value=False)
    client.close = AsyncMock()
    return client


@pytest.fixture
def qa():
    """MultiModelQA with no pool and mocked model router.

    The topic_delivery and internal_consistency gates introduced in
    commit cfe767b6 are stubbed out on the instance level so the existing
    validator + main-critic tests only assert on those two reviewers.
    Dedicated gate tests live in the gates section below.
    """
    async def _skip_gate(*_args, **_kwargs):
        return None

    with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
        instance = MultiModelQA(pool=None, settings_service=None)
    # Stub the new gates so existing tests that don't care about them
    # see the pre-gate reviewer count (validator + main critic = 2).
    instance._check_topic_delivery = _skip_gate  # type: ignore[method-assign]
    instance._check_internal_consistency = _skip_gate  # type: ignore[method-assign]
    return instance


# ---------------------------------------------------------------------------
# Programmatic validator passes -> runs cloud review
# ---------------------------------------------------------------------------


class TestValidatorPasses:
    async def test_passes_validator_runs_cloud_review(self, qa):
        """When programmatic validator passes, cloud review should execute."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client()):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Should have two reviews: programmatic + ollama
        assert len(result.reviews) == 2
        reviewer_names = [r.reviewer for r in result.reviews]
        assert "programmatic_validator" in reviewer_names
        assert "ollama_critic" in reviewer_names

    async def test_all_pass_approved(self, qa):
        """When all reviewers pass and score >= 70, result is approved."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=85.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.approved is True
        assert result.final_score >= 70


# ---------------------------------------------------------------------------
# Programmatic validator fails -> rejects immediately
# ---------------------------------------------------------------------------


class TestValidatorFails:
    async def test_critical_issues_reject_immediately(self, qa):
        """Content with critical validator issues should be rejected without cloud review."""
        with patch("services.multi_model_qa.validate_content", return_value=_failing_validation()):
            result = await qa.review(BAD_TITLE, BAD_CONTENT, "revenue")

        assert result.approved is False
        # Only the programmatic validator should have run
        assert len(result.reviews) == 1
        assert result.reviews[0].reviewer == "programmatic_validator"
        assert result.reviews[0].approved is False

    async def test_rejected_score_reflects_penalty(self, qa):
        """Rejected content should have score reduced by penalty."""
        failing = _failing_validation()
        with patch("services.multi_model_qa.validate_content", return_value=failing):
            result = await qa.review(BAD_TITLE, BAD_CONTENT, "revenue")

        assert result.final_score == max(0, 100 - failing.score_penalty)

    async def test_validation_result_included(self, qa):
        """The ValidationResult should be attached to the MultiModelResult."""
        failing = _failing_validation()
        with patch("services.multi_model_qa.validate_content", return_value=failing):
            result = await qa.review(BAD_TITLE, BAD_CONTENT, "revenue")

        assert result.validation is not None
        assert result.validation.critical_count == 1


# ---------------------------------------------------------------------------
# Weighted score calculation
# ---------------------------------------------------------------------------


class TestWeightedScore:
    async def test_weighted_average_programmatic_40_cloud_60(self, qa):
        """Score should be 40% programmatic + 60% cloud reviewer."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=80.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # programmatic: score=100, weight=0.4  -> 40
        # ollama: score=80, weight=0.6         -> 48
        # total_weight = 1.0
        # final = 88.0
        expected = (100.0 * 0.4 + 80.0 * 0.6) / (0.4 + 0.6)
        assert abs(result.final_score - expected) < 0.1

    async def test_low_cloud_score_can_block_approval(self, qa):
        """Even if validator passes, a low cloud score (<70) blocks approval."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=False, score=30.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Cloud reviewer rejected -> all_passed is False -> not approved
        assert result.approved is False


# ---------------------------------------------------------------------------
# All reviewers pass -> approved
# ---------------------------------------------------------------------------


class TestAllReviewersPass:
    async def test_high_scores_approved(self, qa):
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=95.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.approved is True
        assert all(r.approved for r in result.reviews)

    async def test_result_has_summary(self, qa):
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client()):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        summary = result.summary
        assert "APPROVED" in summary or "REJECTED" in summary
        assert "Score:" in summary


# ---------------------------------------------------------------------------
# Graceful handling when Ollama is down
# ---------------------------------------------------------------------------


class TestOllamaDown:
    async def test_ollama_unhealthy_skips_to_fallback(self, qa):
        """When Ollama is down, review should still complete (fallback or skip)."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client_down()):
                # Also patch Gemini to be unavailable (no API key)
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("GOOGLE_API_KEY", None)
                    os.environ.pop("GEMINI_API_KEY", None)
                    result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Should still return a result, just with only the programmatic review
        assert isinstance(result, MultiModelResult)
        assert result.validation is not None
        # At minimum the programmatic validator ran
        assert any(r.reviewer == "programmatic_validator" for r in result.reviews)

    async def test_ollama_exception_handled_gracefully(self, qa):
        """If OllamaClient raises, the review should still complete."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            mock_client = AsyncMock()
            mock_client.check_health = AsyncMock(side_effect=Exception("connection refused"))
            mock_client.close = AsyncMock()
            with patch("services.ollama_client.OllamaClient", return_value=mock_client):
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("GOOGLE_API_KEY", None)
                    os.environ.pop("GEMINI_API_KEY", None)
                    result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert isinstance(result, MultiModelResult)
        # Programmatic validator still ran
        assert len(result.reviews) >= 1

    async def test_only_validator_when_no_cloud(self, qa):
        """With no cloud reviewers, approval is based on validator + score threshold."""
        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client_down()):
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("GOOGLE_API_KEY", None)
                    os.environ.pop("GEMINI_API_KEY", None)
                    result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # With only programmatic validator (score 100), should still be approved
        # since all (1) reviewers passed and score >= 70
        assert result.final_score >= 70


# ---------------------------------------------------------------------------
# MultiModelResult dataclass
# ---------------------------------------------------------------------------


class TestMultiModelResult:
    def test_summary_format(self):
        result = MultiModelResult(
            approved=True,
            final_score=88.0,
            reviews=[
                ReviewerResult(
                    reviewer="programmatic_validator",
                    approved=True,
                    score=100.0,
                    feedback="No issues found",
                    provider="programmatic",
                ),
                ReviewerResult(
                    reviewer="ollama_critic",
                    approved=True,
                    score=80.0,
                    feedback="Well-written content",
                    provider="ollama",
                ),
            ],
        )
        summary = result.summary
        assert "88/100" in summary
        assert "APPROVED" in summary
        assert "programmatic_validator" in summary
        assert "ollama_critic" in summary

    def test_rejected_summary(self):
        result = MultiModelResult(approved=False, final_score=45.0, reviews=[])
        assert "REJECTED" in result.summary


# ---------------------------------------------------------------------------
# New QA gates: topic_delivery + internal_consistency (issue #178 partial)
# ---------------------------------------------------------------------------


def _mock_gate_client(json_payload: dict):
    """Return a mock OllamaClient that returns a canned JSON response.

    Used for the gate tests — the gates expect a JSON object with
    gate-specific keys (delivers/consistent, score, reason/contradictions).
    """
    import json
    client = AsyncMock()
    client.check_health = AsyncMock(return_value=True)
    client.generate = AsyncMock(return_value={
        "text": json.dumps(json_payload),
        "tokens": 40,
        "prompt_tokens": 600,
    })
    client.close = AsyncMock()
    return client


@pytest.fixture
def raw_qa():
    """MultiModelQA WITHOUT the gate stubs — exercises the real gate methods."""
    with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
        return MultiModelQA(pool=None, settings_service=None)


class TestTopicDeliveryGate:
    async def test_delivers_passes(self, raw_qa):
        """When the body delivers the topic, gate returns approved with high score."""
        payload = {
            "delivers": True,
            "score": 90,
            "reason": "The body faithfully covers the requested topic.",
        }
        with patch("services.ollama_client.OllamaClient", return_value=_mock_gate_client(payload)):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)
        assert review is not None
        assert review.reviewer == "topic_delivery"
        assert review.provider == "consistency_gate"
        assert review.approved is True
        assert review.score == 90

    async def test_bait_and_switch_fails(self, raw_qa):
        """Bait-and-switch topic returns approved=False with low score."""
        payload = {
            "delivers": False,
            "score": 40,
            "reason": "Title promises 11 indie hackers; body names 2 in passing.",
        }
        with patch("services.ollama_client.OllamaClient", return_value=_mock_gate_client(payload)):
            review = await raw_qa._check_topic_delivery("11 solo indie hackers", "Body is an abstract systems essay.")
        assert review is not None
        assert review.approved is False
        assert review.score == 40
        assert "11 indie hackers" in review.feedback or "names 2" in review.feedback

    async def test_empty_topic_skipped(self, raw_qa):
        """Empty topic returns None — nothing to check."""
        review = await raw_qa._check_topic_delivery("", GOOD_CONTENT)
        assert review is None

    async def test_ollama_unhealthy_skipped(self, raw_qa):
        """When Ollama health check fails, gate returns None (skipped)."""
        client = AsyncMock()
        client.check_health = AsyncMock(return_value=False)
        client.close = AsyncMock()
        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)
        assert review is None

    async def test_malformed_json_skipped(self, raw_qa):
        """Unparseable Ollama response returns None."""
        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(return_value={"text": "not json at all", "tokens": 5})
        client.close = AsyncMock()
        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)
        assert review is None


class TestInternalConsistencyGate:
    async def test_consistent_passes(self, raw_qa):
        """No contradictions found returns approved with high score."""
        payload = {
            "consistent": True,
            "score": 92,
            "contradictions": [],
        }
        with patch("services.ollama_client.OllamaClient", return_value=_mock_gate_client(payload)):
            review = await raw_qa._check_internal_consistency(GOOD_CONTENT)
        assert review is not None
        assert review.reviewer == "internal_consistency"
        assert review.provider == "consistency_gate"
        assert review.approved is True
        assert review.score == 92

    async def test_contradiction_fails(self, raw_qa):
        """Contradiction pair in the list returns approved=False with contradictions in feedback."""
        payload = {
            "consistent": False,
            "score": 45,
            "contradictions": [
                "Section 1 says 'no React'; Section 3 recommends Next.js",
                "Section 2 shows custom auth code; Section 4 says 'never build custom auth'",
            ],
        }
        with patch("services.ollama_client.OllamaClient", return_value=_mock_gate_client(payload)):
            review = await raw_qa._check_internal_consistency(GOOD_CONTENT)
        assert review is not None
        assert review.approved is False
        assert review.score == 45
        assert "Contradictions" in review.feedback

    async def test_empty_content_skipped(self, raw_qa):
        """Empty content returns None — nothing to check."""
        review = await raw_qa._check_internal_consistency("")
        assert review is None

    async def test_ollama_unhealthy_skipped(self, raw_qa):
        """Ollama health check failure returns None."""
        client = AsyncMock()
        client.check_health = AsyncMock(return_value=False)
        client.close = AsyncMock()
        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_internal_consistency(GOOD_CONTENT)
        assert review is None

    async def test_exception_returns_none(self, raw_qa):
        """An exception anywhere in the gate is caught and returns None."""
        client = AsyncMock()
        client.check_health = AsyncMock(side_effect=Exception("connection refused"))
        client.close = AsyncMock()
        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_internal_consistency(GOOD_CONTENT)
        assert review is None
