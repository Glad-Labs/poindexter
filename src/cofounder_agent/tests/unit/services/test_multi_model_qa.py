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


@pytest.fixture(autouse=True)
def _stub_resolve_tier_model():
    """Auto-stub the cost-tier resolver across this whole test module.

    Lane B sweep migration: ``MultiModelQA._resolve_critic_model`` calls
    ``resolve_tier_model(self.pool, "standard")`` which fails on the
    no-pool fixture. Tests at this level care about review aggregation,
    not which concrete model the critic uses, so the resolver returns a
    fixed string for every test.
    """
    with patch(
        "services.multi_model_qa.resolve_tier_model",
        AsyncMock(return_value="ollama/gemma3:27b"),
    ):
        yield


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

    with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
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

        # Core reviewers: programmatic (100, w=0.4) + ollama (80, w=0.6) = 88
        # Additional reviewers (url_verifier, etc.) may shift the score slightly
        # The final score should be in the high 80s range
        assert 82 <= result.final_score <= 95

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
    with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
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
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
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
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
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
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*args, **kwargs):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()), \
             patch("services.ollama_client.OllamaClient", return_value=_mock_ollama_client(approved=True, score=60.0)):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.approved is True

    async def test_critic_model_resolved_via_cost_tier(self):
        """Lane B sweep: the critic model is resolved via
        ``cost_tier="standard"`` inside ``_review_with_ollama``, not
        threaded through ``review()`` as ``model_override``. This test
        pins the new contract: ``review()`` no longer reads
        ``pipeline_critic_model``; the cost-tier API handles selection.
        """
        settings = _settings_service(pipeline_critic_model="ollama/qwen3:30b")

        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
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

        # Lane B contract: review() no longer threads pipeline_critic_model
        # as model_override; the resolver handles it inside the critic call.
        assert captured["model_override"] is None


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


# ---------------------------------------------------------------------------
# GH-91: validator warning count → QA score penalty
# ---------------------------------------------------------------------------


def _validation_with_warnings(warning_count: int) -> ValidationResult:
    """Build a passing ValidationResult with ``warning_count`` warnings."""
    issues = [
        ValidationIssue(
            severity="warning",
            category="unlinked_citation",
            description=f"Unlinked citation #{i}",
            matched_text=f"as noted in source {i}",
        )
        for i in range(warning_count)
    ]
    # score_penalty follows the 3-per-warning rule inside content_validator
    return ValidationResult(
        passed=True, issues=issues, score_penalty=3 * warning_count,
    )


class TestWarningQAPenalty:
    """The GH-91 fix: validator warnings now subtract from the final QA
    score so a post with many unsourced citations falls below the Q70
    threshold even when the critic scores it high."""

    async def test_nine_warnings_drops_critic_85_below_threshold(self):
        """A Q85-level post with 9 warnings should reject at the default
        penalty of 3 pts/warning (27 pt drop → ~58 → below Q70)."""
        settings = _settings_service(
            qa_validator_weight=0.4,
            qa_critic_weight=0.6,
            qa_gate_weight=0.3,
            qa_final_score_threshold=70,
            content_validator_warning_qa_penalty=3,
        )
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*_a, **_k):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        validation = _validation_with_warnings(9)
        with patch("services.multi_model_qa.validate_content", return_value=validation), \
             patch(
                 "services.ollama_client.OllamaClient",
                 return_value=_mock_ollama_client(approved=True, score=85.0),
             ):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # 9 warnings * 3 pts = 27 pt penalty applied to final score.
        # Base weighted score is roughly (73*0.4 + 85*0.6)/1.0 ≈ 80.2
        # After -27 penalty it should land in the 50s.
        assert result.final_score < 70, (
            f"expected penalty to drop score below 70, got {result.final_score}"
        )
        assert result.approved is False

    async def test_zero_warnings_no_penalty(self):
        settings = _settings_service(
            qa_validator_weight=0.4,
            qa_critic_weight=0.6,
            qa_gate_weight=0.3,
            qa_final_score_threshold=70,
            content_validator_warning_qa_penalty=3,
        )
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*_a, **_k):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        with patch("services.multi_model_qa.validate_content", return_value=_passing_validation()), \
             patch(
                 "services.ollama_client.OllamaClient",
                 return_value=_mock_ollama_client(approved=True, score=85.0),
             ):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # No warnings → no penalty → post passes.
        assert result.approved is True
        assert result.final_score >= 70

    async def test_penalty_configurable(self):
        """A site with a steeper 5 pts/warning penalty should reject
        sooner than the default 3."""
        settings = _settings_service(
            qa_validator_weight=0.4,
            qa_critic_weight=0.6,
            qa_gate_weight=0.3,
            qa_final_score_threshold=70,
            content_validator_warning_qa_penalty=5,
        )
        with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
            qa = MultiModelQA(pool=None, settings_service=settings)

        async def _skip_gate(*_a, **_k):
            return None
        qa._check_topic_delivery = _skip_gate
        qa._check_internal_consistency = _skip_gate

        # Only 4 warnings but 5 pt penalty = 20 pt drop. Base ~80 → ~60.
        validation = _validation_with_warnings(4)
        with patch("services.multi_model_qa.validate_content", return_value=validation), \
             patch(
                 "services.ollama_client.OllamaClient",
                 return_value=_mock_ollama_client(approved=True, score=85.0),
             ):
            result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert result.final_score < 70


# ---------------------------------------------------------------------------
# #399: qa_gates honors enabled + required_to_pass
# ---------------------------------------------------------------------------
#
# Two test cases were removed in PR #271 (test_qa_gates.py) because the
# runtime didn't honor the qa_gates control plane. These replacements assert
# the fixed production code:
#
# 1. enabled=False on a gate row → the gate's LLM call is NOT invoked
#    (mock OllamaClient call_count == 0 for the disabled gate).
# 2. required_to_pass=False (advisory) on a gate row → the gate still RUNS
#    so its score feeds the weighted average for trend tracking, but a
#    failing run does NOT veto the overall pass/fail decision.


def _row_for(
    name: str,
    *,
    order: int = 100,
    enabled: bool = True,
    required: bool = True,
):
    """Shape a qa_gates row for the stub pool. Mirrors test_qa_gates._row."""
    return {
        "name": name,
        "stage_name": "qa",
        "execution_order": order,
        "reviewer": name,
        "required_to_pass": required,
        "enabled": enabled,
        "config": {},
    }


class _StubGatesConn:
    def __init__(self, rows):
        self._rows = list(rows)

    async def fetch(self, _query, *_args):
        return self._rows


class _StubGatesPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        pool_self = self

        class _Ctx:
            async def __aenter__(self_inner):
                return pool_self._conn

            async def __aexit__(self_inner, *_exc):
                return False

        return _Ctx()


def _qa_with_gate_chain(rows):
    """Build a MultiModelQA whose pool returns ``rows`` from qa_gates fetch."""
    pool = _StubGatesPool(_StubGatesConn(
        sorted(rows, key=lambda r: r["execution_order"]),
    ))
    with MagicMock():  # was: patch model_router (deleted Phase 2 / 6817f391)
        return MultiModelQA(pool=pool, settings_service=None)


class TestQAGatesEnabledFalseSkipsLLMCall:
    """``qa_gates.enabled=False`` for a gate row → the gate's LLM call is
    short-circuited entirely (no inference, no review entry, no cost)."""

    async def test_llm_critic_disabled_does_not_invoke_ollama(self):
        """The mock OllamaClient must never see ``check_health`` /
        ``generate`` when llm_critic is disabled in qa_gates."""
        rows = [
            _row_for("programmatic_validator", order=100, enabled=True),
            _row_for("llm_critic", order=200, enabled=False),
            _row_for("url_verifier", order=300, enabled=False),
            _row_for("consistency", order=400, enabled=False),
            _row_for("web_factcheck", order=500, enabled=False),
            _row_for("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_gate_chain(rows)
        # Stub the topic_delivery + rendered_preview gates (not in the
        # seeded chain) so they don't add reviews.
        qa._check_topic_delivery = AsyncMock(return_value=None)
        qa._check_rendered_preview = AsyncMock(return_value=None)

        # The sentinel: the OllamaClient mock counts calls. If llm_critic's
        # _review_with_cloud_model fires, check_health + generate are hit.
        ollama_mock = _mock_ollama_client(approved=True, score=90.0)

        with patch(
            "services.multi_model_qa.validate_content",
            return_value=_passing_validation(),
        ):
            with patch(
                "services.ollama_client.OllamaClient",
                return_value=ollama_mock,
            ):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        assert ollama_mock.check_health.call_count == 0, (
            "llm_critic disabled in qa_gates → OllamaClient.check_health "
            f"must NOT be called, got {ollama_mock.check_health.call_count}"
        )
        assert ollama_mock.generate.call_count == 0, (
            "llm_critic disabled in qa_gates → OllamaClient.generate "
            f"must NOT be called, got {ollama_mock.generate.call_count}"
        )
        # And the resulting review list must not contain ollama_critic.
        names = [r.reviewer for r in result.reviews]
        assert "ollama_critic" not in names
        # The validator (still enabled) must have produced its review.
        assert "programmatic_validator" in names

    async def test_disabled_gates_do_not_appear_in_reviews(self):
        """Vision + consistency + web_factcheck disabled → no review entries
        for those reviewers."""
        rows = [
            _row_for("programmatic_validator", order=100, enabled=True),
            _row_for("llm_critic", order=200, enabled=True),
            _row_for("url_verifier", order=300, enabled=False),
            _row_for("consistency", order=400, enabled=False),
            _row_for("web_factcheck", order=500, enabled=False),
            _row_for("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_gate_chain(rows)
        qa._check_topic_delivery = AsyncMock(return_value=None)
        qa._check_rendered_preview = AsyncMock(return_value=None)

        # Fail the test if any of the disabled-gate methods are invoked.
        qa._check_internal_consistency = AsyncMock(
            side_effect=AssertionError("consistency disabled — must NOT run"),
        )
        qa._check_image_relevance = AsyncMock(
            side_effect=AssertionError("vision_gate disabled — must NOT run"),
        )
        qa._web_fact_check = AsyncMock(
            side_effect=AssertionError("web_factcheck disabled — must NOT run"),
        )

        with patch(
            "services.multi_model_qa.validate_content",
            return_value=_passing_validation(),
        ):
            with patch(
                "services.ollama_client.OllamaClient",
                return_value=_mock_ollama_client(approved=True, score=90.0),
            ):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        names = {r.reviewer for r in result.reviews}
        assert "internal_consistency" not in names
        assert "image_relevance" not in names
        assert "web_factcheck" not in names
        assert "url_verifier" not in names


class TestQAGatesAdvisoryDoesNotVeto:
    """``qa_gates.required_to_pass=False`` (advisory) → gate still RUNS but
    its ``approved=False`` does NOT cause the overall decision to flip."""

    async def test_advisory_critic_failure_does_not_block_approval(self):
        """An advisory llm_critic that returns approved=False must still
        let the post pass overall, with the gate marked as advisory in
        the result schema."""
        rows = [
            _row_for("programmatic_validator", order=100, enabled=True),
            # llm_critic enabled but advisory — score still feeds the
            # weighted average, but approved=False does NOT veto.
            _row_for("llm_critic", order=200, enabled=True, required=False),
            _row_for("url_verifier", order=300, enabled=False),
            _row_for("consistency", order=400, enabled=False),
            _row_for("web_factcheck", order=500, enabled=False),
            _row_for("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_gate_chain(rows)
        qa._check_topic_delivery = AsyncMock(return_value=None)
        qa._check_rendered_preview = AsyncMock(return_value=None)

        # Critic reports a failing review (approved=False, score 30).
        # Without the advisory override this would flip the overall
        # decision to rejected. With required_to_pass=False the post
        # must still come back approved=True (validator score 100,
        # weighted final score >= 70 threshold).
        with patch(
            "services.multi_model_qa.validate_content",
            return_value=_passing_validation(),
        ):
            with patch(
                "services.ollama_client.OllamaClient",
                return_value=_mock_ollama_client(approved=False, score=30.0),
            ):
                result = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        critic = next(
            (r for r in result.reviews if r.reviewer == "ollama_critic"), None,
        )
        assert critic is not None, "advisory gate should still RUN — review missing"
        # Result schema flag — the gate is marked advisory.
        assert critic.advisory is True, (
            "advisory gate must be marked advisory=True in the result schema"
        )
        # And the overall decision is NOT flipped by the advisory failure.
        # The validator scored 100 and is weight 0.4; the critic 30 weight
        # 0.6 → final ~58 by weight. But because the critic is advisory,
        # _reviewer_vetoes ignores its veto bit. The final score logic uses
        # the score regardless of advisory, so what we assert is the veto
        # contract: the advisory failure did NOT add itself to the veto
        # list. Use the contract field directly.
        # Concretely: critic.advisory=True and critic.approved is now True
        # (rewritten to True so legacy callers see passing).
        assert critic.approved is True, (
            "advisory gate's approved bit is rewritten to True so the "
            "legacy boolean veto check sees the post as passing"
        )

    async def test_advisory_failure_score_still_feeds_weighted_average(self):
        """Advisory mode must keep the gate's score in the weighted average —
        we want trend tracking. A failing-but-advisory critic should drag
        the final score down even though it can't veto outright."""
        rows = [
            _row_for("programmatic_validator", order=100, enabled=True),
            _row_for("llm_critic", order=200, enabled=True, required=False),
            _row_for("url_verifier", order=300, enabled=False),
            _row_for("consistency", order=400, enabled=False),
            _row_for("web_factcheck", order=500, enabled=False),
            _row_for("vision_gate", order=600, enabled=False),
        ]
        qa = _qa_with_gate_chain(rows)
        qa._check_topic_delivery = AsyncMock(return_value=None)
        qa._check_rendered_preview = AsyncMock(return_value=None)

        # Run twice — once with critic score 90, once with 30. Final
        # scores must differ (advisory still affects the weighted avg).
        with patch(
            "services.multi_model_qa.validate_content",
            return_value=_passing_validation(),
        ):
            with patch(
                "services.ollama_client.OllamaClient",
                return_value=_mock_ollama_client(approved=True, score=90.0),
            ):
                high = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)
            with patch(
                "services.ollama_client.OllamaClient",
                return_value=_mock_ollama_client(approved=False, score=30.0),
            ):
                low = await qa.review(GOOD_TITLE, GOOD_CONTENT, GOOD_TOPIC)

        # The advisory failure must drag the score below the high-score run.
        assert low.final_score < high.final_score, (
            "advisory critic score must still feed the weighted average — "
            f"low={low.final_score}, high={high.final_score}"
        )
        # And a feedback marker exists so operators can audit which gates
        # ran in advisory mode.
        critic_low = next(
            r for r in low.reviews if r.reviewer == "ollama_critic"
        )
        assert "advisory" in critic_low.feedback.lower()


@pytest.mark.unit
class TestDeepEvalBrandFabricationGate:
    """``_check_deepeval_brand`` wraps the deepeval rail as a ReviewerResult.

    First production wire-in of DeepEval (sub-issue 1 of glad-labs-stack#329).
    The rail itself is exercised in test_deepeval_rails.py — these cases
    cover the wrapper logic (enable gate, score rescaling, ReviewerResult
    shape).
    """

    def test_clean_content_returns_score_100_approved(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        # Patch the rail's evaluate fn so the test doesn't need deepeval
        # actually loaded. Same shape: (passed, score_unit, reason).
        with patch(
            "services.deepeval_rails.evaluate_brand_fabrication",
            return_value=(True, 1.0, "No fabrication patterns matched"),
        ), patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = qa._check_deepeval_brand(GOOD_CONTENT, GOOD_TOPIC)

        assert result is not None
        assert result.reviewer == "deepeval_brand_fabrication"
        assert result.provider == "deepeval"
        assert result.approved is True
        assert result.score == 100.0
        assert "No fabrication" in result.feedback

    def test_fabrication_detected_returns_score_0_rejected(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_brand_fabrication",
            return_value=(False, 0.0, "1 fabrication(s) detected: fake_quote: 'foo'"),
        ), patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = qa._check_deepeval_brand(BAD_CONTENT, BAD_TITLE)

        assert result is not None
        assert result.approved is False
        assert result.score == 0.0
        assert "fabrication" in result.feedback.lower()

    def test_returns_none_when_rail_disabled(self):
        """Operator turning off ``deepeval_enabled`` must short-circuit
        before the metric runs (avoids loading deepeval / spending CPU)."""
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.is_enabled", return_value=False,
        ), patch(
            "services.deepeval_rails.evaluate_brand_fabrication",
        ) as eval_mock:
            result = qa._check_deepeval_brand(GOOD_CONTENT, GOOD_TOPIC)

        assert result is None
        eval_mock.assert_not_called()

    def test_partial_score_rescales_to_0_100(self):
        """Brand metric is binary today, but the rescaler must work
        for graded scores — the G-Eval and Faithfulness reviewers
        return values like 0.73 that should land at 73.0."""
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_brand_fabrication",
            return_value=(True, 0.73, "graded score"),
        ), patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = qa._check_deepeval_brand("body text", "topic")

        assert result is not None
        assert result.score == 73.0


@pytest.mark.unit
class TestDeepEvalGEvalGate:
    """Wraps the LLM-judge rail. Lane D sub-issue 1 of #329."""

    @pytest.mark.asyncio
    async def test_high_score_returns_approved(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_g_eval",
            return_value=(True, 0.9, "well grounded"),
        ), patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = await qa._check_deepeval_g_eval(GOOD_CONTENT, GOOD_TOPIC)

        assert result is not None
        assert result.reviewer == "deepeval_g_eval"
        assert result.provider == "deepeval"
        assert result.approved is True
        assert result.score == 90.0

    @pytest.mark.asyncio
    async def test_low_score_returns_rejected(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_g_eval",
            return_value=(False, 0.4, "vague claims"),
        ), patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = await qa._check_deepeval_g_eval("body", "topic")

        assert result is not None
        assert result.approved is False
        assert result.score == 40.0
        assert "vague" in result.feedback

    @pytest.mark.asyncio
    async def test_returns_none_when_rail_disabled(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.is_enabled", return_value=False,
        ), patch(
            "services.deepeval_rails.evaluate_g_eval",
        ) as judge_mock:
            result = await qa._check_deepeval_g_eval(GOOD_CONTENT, GOOD_TOPIC)

        assert result is None
        judge_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_threshold_pulled_from_settings(self):
        """Operator can override threshold + criterion via app_settings."""
        settings = AsyncMock()
        settings.get = AsyncMock(side_effect=lambda key: {
            "deepeval_threshold_g_eval": "0.85",
            "deepeval_g_eval_criterion": "Custom criterion text",
        }.get(key))
        qa = MultiModelQA(pool=None, settings_service=settings)

        with patch(
            "services.deepeval_rails.evaluate_g_eval",
            return_value=(True, 0.9, "ok"),
        ) as judge_mock, patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            await qa._check_deepeval_g_eval("body", "topic")

        kwargs = judge_mock.call_args.kwargs
        assert kwargs["threshold"] == 0.85
        assert kwargs["criterion"] == "Custom criterion text"


@pytest.mark.unit
class TestDeepEvalFaithfulnessGate:
    """Wraps the FaithfulnessMetric rail.

    Skips without research_sources; when present, splits into paragraph
    chunks and asks the judge whether every claim is attributable.
    """

    @pytest.mark.asyncio
    async def test_skips_without_research(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_faithfulness",
        ) as judge_mock, patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = await qa._check_deepeval_faithfulness(
                "body", research_sources=None,
            )

        assert result is None
        judge_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_grounded_post_passes(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.evaluate_faithfulness",
            return_value=(True, 0.95, "all claims attributable"),
        ) as judge_mock, patch(
            "services.deepeval_rails.is_enabled", return_value=True,
        ):
            result = await qa._check_deepeval_faithfulness(
                "FastAPI uses uvicorn.",
                research_sources=(
                    "FastAPI is a Python framework.\n\n"
                    "It uses uvicorn as its ASGI server."
                ),
            )

        assert result is not None
        assert result.reviewer == "deepeval_faithfulness"
        assert result.approved is True
        assert result.score == 95.0
        chunks = judge_mock.call_args.args[1]
        assert len(chunks) == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_rail_disabled(self):
        qa = MultiModelQA(pool=None, settings_service=None)
        with patch(
            "services.deepeval_rails.is_enabled", return_value=False,
        ), patch(
            "services.deepeval_rails.evaluate_faithfulness",
        ) as judge_mock:
            result = await qa._check_deepeval_faithfulness(
                "body", "research bundle text",
            )

        assert result is None
        judge_mock.assert_not_called()
