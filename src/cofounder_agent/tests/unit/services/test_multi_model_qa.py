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

GOOD_TITLE = "How to Deploy FastAPI on Fly.io"
GOOD_CONTENT = (
    "Fly.io makes it easy to deploy Python web applications. "
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

        # Should have at least programmatic + ollama (url_verifier may also run)
        assert len(result.reviews) >= 2
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


class TestConsistencyGateVetoPolicy:
    """The consistency gate is advisory — it only vetoes when its own score
    is unambiguously low (< qa_consistency_veto_threshold, default 50).
    Prevents flaky contradiction reports from rejecting otherwise strong
    posts (fixes the 81% same-day rejection rate from 2026-04-10)."""

    async def test_moderate_inconsistency_does_not_veto(self, qa):
        """A consistency gate with approved=False but score 60 should NOT
        reject an article that otherwise scores high."""
        from services.multi_model_qa import ReviewerResult

        async def _consistency_moderate(_content):
            return ReviewerResult(
                reviewer="internal_consistency",
                approved=False,
                score=60.0,
                feedback="Contradictions: might be something",
                provider="consistency_gate",
            )

        qa._check_internal_consistency = _consistency_moderate  # type: ignore[method-assign]

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=90.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Final score is still well above threshold and gate didn't hard-veto
        assert result.final_score >= 70
        assert result.approved is True

    async def test_unambiguous_inconsistency_still_vetoes(self, qa):
        """A consistency gate with a clearly low score (< 50) should still
        veto — real contradictions should manifest in the gate's own score."""
        from services.multi_model_qa import ReviewerResult

        async def _consistency_low(_content):
            return ReviewerResult(
                reviewer="internal_consistency",
                approved=False,
                score=20.0,
                feedback="Contradictions: Section 1 says X, Section 3 says not-X",
                provider="consistency_gate",
            )

        qa._check_internal_consistency = _consistency_low  # type: ignore[method-assign]

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            with patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=90.0)):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.approved is False


# ---------------------------------------------------------------------------
# Settings-driven weight and threshold overrides
# ---------------------------------------------------------------------------


def _settings_service(**overrides):
    """Return a mock settings service whose .get() returns overrides."""
    svc = MagicMock()

    async def _get(key):
        return overrides.get(key)

    svc.get = AsyncMock(side_effect=_get)
    return svc


class TestSettingsOverrides:
    async def test_custom_validator_weight_from_settings(self):
        settings = _settings_service(
            qa_validator_weight=0.5,
            qa_critic_weight=0.5,
            qa_gate_weight=0.0,
            qa_final_score_threshold=70,
        )
        with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
            qa = MultiModelQA(pool=None, settings_service=settings)

        # Stub gates so they don't actually call Ollama
        async def _skip_gate(*args, **kwargs):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()), \
             patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(score=80.0)):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # With 50/50 weights, final_score should be between validator (100) and critic (80)
        assert 80 <= result.final_score <= 100

    async def test_high_threshold_rejects_decent_content(self):
        """If the configured threshold is 95, a 80-score critic gets rejected."""
        settings = _settings_service(
            qa_validator_weight=0.4,
            qa_critic_weight=0.6,
            qa_gate_weight=0.3,
            qa_final_score_threshold=95,
        )
        with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*args, **kwargs):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()), \
             patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(score=75.0)):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Weighted final score is somewhere around (100*0.4 + 75*0.6)/1.0 = 85
        # 85 < 95 threshold → rejected
        assert result.approved is False

    async def test_low_threshold_approves_weak_content(self):
        settings = _settings_service(
            qa_validator_weight=0.4,
            qa_critic_weight=0.6,
            qa_gate_weight=0.3,
            qa_final_score_threshold=50,  # very lenient
        )
        with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*args, **kwargs):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()), \
             patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=60.0)):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.approved is True

    async def test_custom_critic_model_passed_through(self):
        """settings.get('pipeline_critic_model') is passed as model_override."""
        settings = _settings_service(pipeline_critic_model="ollama/qwen3:30b")

        with patch("services.multi_model_qa.get_model_router", return_value=MagicMock()):
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*args, **kwargs):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        captured = {}

        async def _capture_review(title, content, topic, model_override=None, research_sources=None):
            captured["model_override"] = model_override
            return None  # Skip critic path

        qa._review_with_cloud_model = _capture_review

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert captured["model_override"] == "ollama/qwen3:30b"


# ---------------------------------------------------------------------------
# research_sources threading
# ---------------------------------------------------------------------------


class TestResearchSourcesThreading:
    async def test_research_sources_passed_to_cloud_review(self, qa):
        """When review() is called with research_sources, it flows to _review_with_cloud_model."""
        captured = {}

        async def _capture(title, content, topic, model_override=None, research_sources=None):
            captured["research_sources"] = research_sources
            return None  # Skip the critic

        qa._review_with_cloud_model = _capture

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            await qa.review(
                GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC,
                research_sources="Source 1: github.com/example\nSource 2: arxiv.org/abs/1234",
            )

        assert "github.com" in captured["research_sources"]
        assert "arxiv.org" in captured["research_sources"]

    async def test_none_research_sources_by_default(self, qa):
        captured = {}

        async def _capture(title, content, topic, model_override=None, research_sources=None):
            captured["research_sources"] = research_sources
            return None

        qa._review_with_cloud_model = _capture

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()):
            await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert captured["research_sources"] is None


# ---------------------------------------------------------------------------
# Critic-skipped path: final score falls back to validator only
# ---------------------------------------------------------------------------


class TestCriticSkippedFinalScore:
    async def test_critic_skipped_uses_validator_score_only(self, qa):
        """When cross-model review returns None, final_score = validator.score (not weighted)."""

        async def _no_cloud(*args, **kwargs):
            return None  # Critic skipped

        qa._review_with_cloud_model = _no_cloud

        validator_passing_high = ValidationResult(
            passed=True, issues=[], score_penalty=0,
        )

        with patch("services.multi_model_qa.validate_content", return_value=validator_passing_high):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # Validator score is 100 - 0 = 100
        assert result.final_score == 100.0

    async def test_critic_skipped_with_penalized_validator(self, qa):
        """When critic is skipped, validator penalty applies to final score directly."""
        async def _no_cloud(*args, **kwargs):
            return None

        qa._review_with_cloud_model = _no_cloud

        validator_with_warnings = ValidationResult(
            passed=True,  # still passes (no critical)
            issues=[
                ValidationIssue(
                    severity="warning",
                    category="fake_stat",
                    description="Possible fabricated stat",
                    matched_text="50% improvement",
                )
            ],
            score_penalty=15,
        )

        with patch("services.multi_model_qa.validate_content", return_value=validator_with_warnings):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.final_score == 85.0  # 100 - 15
        # Still approved since 85 >= 70 default threshold
        assert result.approved is True

    async def test_critic_skipped_below_threshold_rejected(self, qa):
        """Heavy validator penalty below 70 rejects even when no critical issues."""
        async def _no_cloud(*args, **kwargs):
            return None

        qa._review_with_cloud_model = _no_cloud

        heavy_penalty = ValidationResult(
            passed=True,  # warnings only
            issues=[
                ValidationIssue(
                    severity="warning", category="brand_contradiction",
                    description="OpenAI API reference", matched_text="OpenAI API",
                )
            ],
            score_penalty=40,  # 100 - 40 = 60, below 70 threshold
        )

        with patch("services.multi_model_qa.validate_content", return_value=heavy_penalty):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.final_score == 60.0
        assert result.approved is False


# ---------------------------------------------------------------------------
# _run_gate_prompt — additional branches via the _check_topic_delivery proxy
# ---------------------------------------------------------------------------


class TestGatePromptBranches:
    async def test_json_in_code_fence_extracted(self, raw_qa):
        """Gates should extract JSON from markdown code fences."""
        import json
        fenced = "```json\n" + json.dumps({
            "delivers": True,
            "score": 85,
            "reason": "Good coverage",
        }) + "\n```"

        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(return_value={"text": fenced, "tokens": 40})
        client.close = AsyncMock()

        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)

        assert review is not None
        assert review.score == 85
        assert review.approved is True

    async def test_json_embedded_in_prose_extracted(self, raw_qa):
        """Gates should fall back to regex-extracting a JSON object from prose."""
        mixed = 'The editor says: {"delivers": false, "score": 30, "reason": "Off-topic"} — that is all.'

        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(return_value={"text": mixed, "tokens": 40})
        client.close = AsyncMock()

        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)

        assert review is not None
        assert review.approved is False
        assert review.score == 30

    async def test_empty_generate_response_returns_none(self, raw_qa):
        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(return_value={"text": "", "tokens": 0})
        client.close = AsyncMock()

        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)

        assert review is None

    async def test_generate_timeout_returns_none(self, raw_qa):
        """asyncio.TimeoutError during generate returns None (gate skipped)."""
        import asyncio
        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(side_effect=asyncio.TimeoutError())
        client.close = AsyncMock()

        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_topic_delivery(GOOD_TOPIC, GOOD_CONTENT)

        assert review is None

    async def test_consistency_gate_list_of_contradictions_joined(self, raw_qa):
        """Contradictions list is joined with semicolons in the feedback."""
        payload = {
            "consistent": False,
            "score": 30,
            "contradictions": [
                "First contradiction",
                "Second contradiction",
                "Third contradiction",
            ],
        }

        client = AsyncMock()
        client.check_health = AsyncMock(return_value=True)
        client.generate = AsyncMock(return_value={
            "text": __import__("json").dumps(payload),
            "tokens": 40,
        })
        client.close = AsyncMock()

        with patch("services.ollama_client.OllamaClient", return_value=client):
            review = await raw_qa._check_internal_consistency(GOOD_CONTENT)

        assert review is not None
        assert "First contradiction" in review.feedback
        assert "Second contradiction" in review.feedback
        assert ";" in review.feedback
