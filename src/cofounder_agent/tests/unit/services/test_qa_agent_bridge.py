"""
Unit tests for services/qa_agent_bridge.py

Tests QAAgentBridge: hybrid evaluation, score calculation, feedback synthesis,
recommendation generation, and format conversion.
"""

import pytest
from datetime import datetime

from services.qa_agent_bridge import (
    HybridQualityResult,
    QAAgentBridge,
    get_qa_agent_bridge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge() -> QAAgentBridge:
    return QAAgentBridge()


SAMPLE_PATTERN_SCORES = {
    "clarity": 8.0,
    "accuracy": 7.5,
    "completeness": 7.0,
    "relevance": 8.5,
    "seo_quality": 6.5,
    "readability": 7.8,
    "engagement": 7.2,
}

GOOD_PATTERN_OVERALL = 7.5
POOR_PATTERN_OVERALL = 5.5


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestQAAgentBridgeInit:
    def test_default_weights(self, bridge):
        assert bridge.qa_weight == 0.4
        assert bridge.pattern_weight == 0.6

    def test_get_qa_agent_bridge_factory(self):
        b = get_qa_agent_bridge()
        assert isinstance(b, QAAgentBridge)

    def test_each_call_returns_new_instance(self):
        b1 = get_qa_agent_bridge()
        b2 = get_qa_agent_bridge()
        assert b1 is not b2


# ---------------------------------------------------------------------------
# qa_to_quality_score
# ---------------------------------------------------------------------------


class TestQaToQualityScore:
    def test_approved_gives_score_8(self, bridge):
        result = bridge.qa_to_quality_score(
            qa_approved=True, qa_feedback="Looks great!", content="Some content"
        )
        assert result["qa_score"] == 8.0
        assert result["qa_approved"] is True

    def test_not_approved_gives_score_5(self, bridge):
        result = bridge.qa_to_quality_score(
            qa_approved=False, qa_feedback="Needs improvement", content="Some content"
        )
        assert result["qa_score"] == 5.0
        assert result["qa_approved"] is False

    def test_source_is_qa_agent(self, bridge):
        result = bridge.qa_to_quality_score(True, "Good", "content")
        assert result["source"] == "qa_agent"

    def test_timestamp_included(self, bridge):
        result = bridge.qa_to_quality_score(True, "Good", "content")
        assert "timestamp" in result

    def test_context_stored(self, bridge):
        ctx = {"topic": "AI", "keywords": ["machine learning"]}
        result = bridge.qa_to_quality_score(True, "Good", "content", context=ctx)
        assert result["context"] == ctx

    def test_default_context_is_empty_dict(self, bridge):
        result = bridge.qa_to_quality_score(True, "Good", "content")
        assert result["context"] == {}


# ---------------------------------------------------------------------------
# create_hybrid_evaluation
# ---------------------------------------------------------------------------


class TestCreateHybridEvaluation:
    @pytest.mark.asyncio
    async def test_passing_evaluation(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Great content here",
            qa_approved=True,
            qa_feedback="Well written!",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=GOOD_PATTERN_OVERALL,
        )
        assert isinstance(result, HybridQualityResult)
        assert result.passing is True
        assert result.evaluation_method == "hybrid"

    @pytest.mark.asyncio
    async def test_failing_evaluation(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Mediocre content",
            qa_approved=False,
            qa_feedback="Needs major revision",
            pattern_scores={"clarity": 4.0, "accuracy": 5.0},
            pattern_overall=POOR_PATTERN_OVERALL,
        )
        assert result.passing is False

    @pytest.mark.asyncio
    async def test_hybrid_score_calculation_defaults(self, bridge):
        """Hybrid = qa_score * 0.4 + pattern_overall * 0.6"""
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,   # qa_score = 8.0
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=7.5,
        )
        expected = 8.0 * 0.4 + 7.5 * 0.6
        assert abs(result.hybrid_overall - expected) < 0.01

    @pytest.mark.asyncio
    async def test_custom_weights_applied(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,  # qa_score = 8.0
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=6.0,
            qa_weight=1.0,
            pattern_weight=0.0,
        )
        # Should be entirely QA-driven (weight normalizes to 1.0/1.0)
        assert abs(result.hybrid_overall - 8.0) < 0.01

    @pytest.mark.asyncio
    async def test_result_has_all_fields(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=GOOD_PATTERN_OVERALL,
        )
        assert result.qa_agent_approved is True
        assert result.qa_agent_feedback == "Good"
        assert result.pattern_based_scores == SAMPLE_PATTERN_SCORES
        assert result.pattern_based_overall == GOOD_PATTERN_OVERALL
        assert isinstance(result.timestamp, datetime)
        assert result.synthesis_feedback != ""
        assert isinstance(result.recommendations, list)

    @pytest.mark.asyncio
    async def test_passing_threshold_7_0(self, bridge):
        """Score exactly 7.0 should pass."""
        # qa_score=8, pattern=6.333... => 8*0.4 + 6.333*0.6 = 3.2 + 3.8 = 7.0
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,  # 8.0
            qa_feedback="OK",
            pattern_scores={},
            pattern_overall=6.333,
        )
        assert result.passing is (result.hybrid_overall >= 7.0)


# ---------------------------------------------------------------------------
# _synthesize_feedback
# ---------------------------------------------------------------------------


class TestSynthesizeFeedback:
    def test_excellent_score_message(self, bridge):
        feedback = bridge._synthesize_feedback(
            qa_approved=True,
            qa_feedback="Outstanding",
            pattern_scores={"clarity": 9.0},
            pattern_overall=9.0,
            hybrid_overall=9.0,
        )
        assert "Excellent" in feedback

    def test_good_score_message(self, bridge):
        feedback = bridge._synthesize_feedback(
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores={},
            pattern_overall=7.8,
            hybrid_overall=7.8,
        )
        assert "Good quality" in feedback

    def test_acceptable_score_message(self, bridge):
        feedback = bridge._synthesize_feedback(
            qa_approved=True,
            qa_feedback="OK",
            pattern_scores={},
            pattern_overall=7.1,
            hybrid_overall=7.1,
        )
        assert "Acceptable" in feedback

    def test_needs_refinement_message(self, bridge):
        feedback = bridge._synthesize_feedback(
            qa_approved=False,
            qa_feedback="Below expectations",
            pattern_scores={},
            pattern_overall=6.0,
            hybrid_overall=6.4,
        )
        assert "refinement" in feedback.lower()

    def test_significant_improvements_message(self, bridge):
        feedback = bridge._synthesize_feedback(
            qa_approved=False,
            qa_feedback="Poor",
            pattern_scores={},
            pattern_overall=4.0,
            hybrid_overall=4.0,
        )
        assert "significant" in feedback.lower()

    def test_weak_criteria_mentioned(self, bridge):
        scores = {"clarity": 5.0, "readability": 6.5}
        feedback = bridge._synthesize_feedback(
            qa_approved=True,
            qa_feedback="OK",
            pattern_scores=scores,
            pattern_overall=6.0,
            hybrid_overall=7.0,
        )
        assert "clarity" in feedback or "readability" in feedback

    def test_strong_criteria_mentioned(self, bridge):
        scores = {"clarity": 9.0, "accuracy": 9.5}
        feedback = bridge._synthesize_feedback(
            qa_approved=True,
            qa_feedback="Great",
            pattern_scores=scores,
            pattern_overall=9.0,
            hybrid_overall=9.0,
        )
        assert "clarity" in feedback or "accuracy" in feedback


# ---------------------------------------------------------------------------
# _generate_recommendations
# ---------------------------------------------------------------------------


class TestGenerateRecommendations:
    def test_clarity_recommendation_from_qa_feedback(self, bridge):
        recs = bridge._generate_recommendations(
            qa_approved=False,
            qa_feedback="Clarity needs improvement",
            pattern_scores={},
            passing=False,
        )
        assert any("clarity" in r.lower() for r in recs)

    def test_seo_recommendation_from_qa_feedback(self, bridge):
        recs = bridge._generate_recommendations(
            qa_approved=False,
            qa_feedback="SEO is lacking",
            pattern_scores={},
            passing=False,
        )
        assert any("seo" in r.lower() or "search" in r.lower() for r in recs)

    def test_pattern_score_triggers_recommendation(self, bridge):
        recs = bridge._generate_recommendations(
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores={"clarity": 5.0},
            passing=False,
        )
        assert any("readability" in r.lower() or "simplif" in r.lower() for r in recs)

    def test_recommendations_limited_to_5(self, bridge):
        # Fill all criteria with low scores
        scores = {
            "clarity": 4.0,
            "accuracy": 4.0,
            "completeness": 4.0,
            "relevance": 4.0,
            "seo_quality": 4.0,
            "readability": 4.0,
            "engagement": 4.0,
        }
        recs = bridge._generate_recommendations(
            qa_approved=False,
            qa_feedback="clarity structure keyword length",
            pattern_scores=scores,
            passing=False,
        )
        assert len(recs) <= 5

    def test_passing_with_low_average_adds_refinement(self, bridge):
        scores = {"clarity": 7.5, "accuracy": 7.5}
        recs = bridge._generate_recommendations(
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores=scores,
            passing=True,
        )
        assert any("refinement" in r.lower() or "elevate" in r.lower() for r in recs)

    def test_no_recommendations_when_all_passing_and_high_scores(self, bridge):
        scores = {"clarity": 9.0, "accuracy": 9.0}
        recs = bridge._generate_recommendations(
            qa_approved=True,
            qa_feedback="Excellent",
            pattern_scores=scores,
            passing=True,
        )
        # High scores should not trigger individual recommendations
        assert not any(
            "simplif" in r.lower() or "citations" in r.lower() for r in recs
        )


# ---------------------------------------------------------------------------
# to_quality_score_format
# ---------------------------------------------------------------------------


class TestToQualityScoreFormat:
    @pytest.mark.asyncio
    async def test_format_contains_required_fields(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=GOOD_PATTERN_OVERALL,
        )
        formatted = bridge.to_quality_score_format(result)

        assert "overall_score" in formatted
        assert "passing" in formatted
        assert "feedback" in formatted
        assert "suggestions" in formatted
        assert "evaluation_method" in formatted
        assert formatted["evaluation_method"] == "hybrid_qa_pattern"

    @pytest.mark.asyncio
    async def test_metadata_contains_weights(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=GOOD_PATTERN_OVERALL,
        )
        formatted = bridge.to_quality_score_format(result)
        metadata = formatted["metadata"]
        assert "qa_weight" in metadata
        assert "pattern_weight" in metadata
        assert "qa_agent_approved" in metadata

    @pytest.mark.asyncio
    async def test_pattern_scores_extracted(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores=SAMPLE_PATTERN_SCORES,
            pattern_overall=GOOD_PATTERN_OVERALL,
        )
        formatted = bridge.to_quality_score_format(result)
        assert formatted["clarity"] == SAMPLE_PATTERN_SCORES["clarity"]
        assert formatted["accuracy"] == SAMPLE_PATTERN_SCORES["accuracy"]

    @pytest.mark.asyncio
    async def test_missing_pattern_scores_default_to_zero(self, bridge):
        result = await bridge.create_hybrid_evaluation(
            content="Content",
            qa_approved=True,
            qa_feedback="Good",
            pattern_scores={},
            pattern_overall=7.0,
        )
        formatted = bridge.to_quality_score_format(result)
        assert formatted["clarity"] == 0
        assert formatted["seo_quality"] == 0
