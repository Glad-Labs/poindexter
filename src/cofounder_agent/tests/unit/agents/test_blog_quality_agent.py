"""
Unit tests for agents/blog_quality_agent.py — BlogQualityAgent
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.blog_quality_agent import BlogQualityAgent, get_blog_quality_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assessment(score=85.0, passing=True):
    """Build a mock QualityAssessment-like object."""
    dims = MagicMock()
    dims.clarity = 80.0
    dims.accuracy = 90.0
    dims.completeness = 85.0
    dims.relevance = 88.0
    dims.seo_quality = 75.0
    dims.readability = 82.0
    dims.engagement = 78.0

    assessment = MagicMock()
    assessment.overall_score = score
    assessment.dimensions = dims
    assessment.passing = passing
    assessment.feedback = "Good content"
    assessment.suggestions = ["Add more examples"]
    method = MagicMock()
    method.value = "pattern-based"
    assessment.evaluation_method = method
    return assessment


def make_agent():
    """Build a BlogQualityAgent with a mocked quality service."""
    with patch("agents.blog_quality_agent.get_quality_service") as mock_factory:
        mock_svc = AsyncMock()
        mock_factory.return_value = mock_svc
        agent = BlogQualityAgent()
        agent.quality_service = mock_svc
    return agent, mock_svc


GOOD_CONTENT = (
    "This is a well-written blog post about artificial intelligence and its applications."
)


# ---------------------------------------------------------------------------
# run() — success paths
# ---------------------------------------------------------------------------


class TestRunSuccess:
    @pytest.mark.asyncio
    async def test_returns_success_status(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment(85.0, True))
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_returns_overall_score(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment(72.5, True))
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["overall_score"] == 72.5

    @pytest.mark.asyncio
    async def test_returns_dimension_scores(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["clarity"] == 80.0
        assert result["accuracy"] == 90.0
        assert result["completeness"] == 85.0
        assert result["relevance"] == 88.0
        assert result["seo_quality"] == 75.0
        assert result["readability"] == 82.0
        assert result["engagement"] == 78.0

    @pytest.mark.asyncio
    async def test_passing_true_when_score_high(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment(90.0, True))
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["passing"] is True

    @pytest.mark.asyncio
    async def test_passing_false_when_score_low(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment(50.0, False))
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["passing"] is False

    @pytest.mark.asyncio
    async def test_feedback_and_suggestions_returned(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["feedback"] == "Good content"
        assert result["suggestions"] == ["Add more examples"]

    @pytest.mark.asyncio
    async def test_evaluation_method_in_result(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["evaluation_method"] == "pattern-based"

    @pytest.mark.asyncio
    async def test_topic_passed_as_context(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        await agent.run({"content": GOOD_CONTENT, "topic": "machine learning"})
        call_kwargs = svc.evaluate.call_args.kwargs
        assert call_kwargs["context"] == {"topic": "machine learning"}

    @pytest.mark.asyncio
    async def test_store_result_passed_through(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        await agent.run({"content": GOOD_CONTENT, "store_result": False})
        call_kwargs = svc.evaluate.call_args.kwargs
        assert call_kwargs["store_result"] is False

    @pytest.mark.asyncio
    async def test_default_store_result_is_true(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        await agent.run({"content": GOOD_CONTENT})
        call_kwargs = svc.evaluate.call_args.kwargs
        assert call_kwargs["store_result"] is True


# ---------------------------------------------------------------------------
# Evaluation method string conversion
# ---------------------------------------------------------------------------


class TestEvaluationMethodConversion:
    @pytest.mark.asyncio
    async def test_valid_method_string_passed_to_service(self):
        from services.quality_service import EvaluationMethod

        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        await agent.run({"content": GOOD_CONTENT, "evaluation_method": "pattern-based"})
        call_kwargs = svc.evaluate.call_args.kwargs
        assert call_kwargs["method"] == EvaluationMethod.PATTERN_BASED

    @pytest.mark.asyncio
    async def test_invalid_method_falls_back_to_pattern_based(self):
        from services.quality_service import EvaluationMethod

        agent, svc = make_agent()
        svc.evaluate = AsyncMock(return_value=_make_assessment())
        await agent.run({"content": GOOD_CONTENT, "evaluation_method": "totally-invalid"})
        call_kwargs = svc.evaluate.call_args.kwargs
        assert call_kwargs["method"] == EvaluationMethod.PATTERN_BASED


# ---------------------------------------------------------------------------
# run() — validation errors
# ---------------------------------------------------------------------------


class TestRunValidation:
    @pytest.mark.asyncio
    async def test_empty_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": ""})
        assert result["status"] == "failed"
        assert result["overall_score"] == 0.0

    @pytest.mark.asyncio
    async def test_short_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": "short"})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_whitespace_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({"content": "   "})
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_missing_content_returns_failed(self):
        agent, _ = make_agent()
        result = await agent.run({})
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# run() — error handling
# ---------------------------------------------------------------------------


class TestRunErrorHandling:
    @pytest.mark.asyncio
    async def test_service_exception_returns_failed(self):
        agent, svc = make_agent()
        svc.evaluate = AsyncMock(side_effect=RuntimeError("Quality service crashed"))
        result = await agent.run({"content": GOOD_CONTENT})
        assert result["status"] == "failed"
        assert "Quality service crashed" in result["error"]
        assert result["overall_score"] == 0.0


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_get_blog_quality_agent_returns_instance(self):
        with patch("agents.blog_quality_agent.get_quality_service"):
            agent = get_blog_quality_agent()
        assert isinstance(agent, BlogQualityAgent)

    def test_factory_accepts_optional_params(self):
        with patch("agents.blog_quality_agent.get_quality_service") as mock_factory:
            mock_factory.return_value = MagicMock()
            db = MagicMock()
            agent = get_blog_quality_agent(database_service=db)
            mock_factory.assert_called_once_with(database_service=db)
        assert isinstance(agent, BlogQualityAgent)
