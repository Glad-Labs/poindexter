"""
Unit tests for UnifiedQualityService pattern-based scoring methods.

Tests the deterministic heuristic functions without any database or LLM calls.
All tests are synchronous — the scoring helpers are pure functions.
"""

import pytest

from services.quality_service import QualityDimensions, UnifiedQualityService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> UnifiedQualityService:
    """Service instance with no dependencies (no DB, no LLM)."""
    return UnifiedQualityService()


# ---------------------------------------------------------------------------
# _score_accuracy
# ---------------------------------------------------------------------------


class TestScoreAccuracy:
    def test_baseline_plain_text(self, svc):
        """Plain text with no citations starts at neutral baseline."""
        score = svc._score_accuracy("This is plain text with no sources.", {})
        assert score == pytest.approx(7.0, abs=0.5)

    def test_inline_citation_raises_score(self, svc):
        """Content with [1]-style inline citations should score above baseline."""
        content = "Scientists discovered this [1]. Further studies confirm it [2]."
        score = svc._score_accuracy(content, {})
        assert score > 6.0

    def test_according_to_raises_score(self, svc):
        """'according to' attribution phrase should bump accuracy."""
        content = "According to the WHO, vaccines are effective."
        score = svc._score_accuracy(content, {})
        assert score > 6.0

    def test_research_shows_raises_score(self, svc):
        """'research shows' phrase should bump accuracy."""
        content = "Research shows that exercise reduces stress significantly."
        score = svc._score_accuracy(content, {})
        assert score > 6.0

    def test_external_link_raises_score(self, svc):
        """An external URL is a strong accuracy signal."""
        content = "See the full study at https://example.com/study for details."
        score = svc._score_accuracy(content, {})
        assert score > 6.0

    def test_multiple_signals_stack(self, svc):
        """Content with several accuracy signals scores higher than content with one."""
        weak = "Research shows this is true."
        strong = (
            "Research shows this is true [1]. According to the study at "
            "https://example.com the results are clear. Published in Nature."
        )
        score_weak = svc._score_accuracy(weak, {})
        score_strong = svc._score_accuracy(strong, {})
        assert score_strong > score_weak

    def test_score_capped_at_10(self, svc):
        """Score must never exceed 10 regardless of signal count."""
        content = (
            "[1] [2] [3] [4] According to research shows studies show "
            "https://a.com https://b.com https://c.com published in source:"
        )
        score = svc._score_accuracy(content, {})
        assert score <= 10.0


# ---------------------------------------------------------------------------
# _score_completeness
# ---------------------------------------------------------------------------


class TestScoreCompleteness:
    def test_short_content_scores_low(self, svc):
        """Very short content (< 100 words) scores below 4."""
        content = "Short article."
        score = svc._score_completeness(content, {})
        assert score < 4.0

    def test_medium_content_scores_mid(self, svc):
        """~500-word content should score around 3-6."""
        content = " ".join(["word"] * 549) + " end."
        score = svc._score_completeness(content, {})
        assert 3.0 <= score <= 7.0

    def test_long_content_with_headings_scores_high(self, svc):
        """2000+ words with multiple headings should approach maximum."""
        headings = "\n".join([f"## Section {i}\n\nSome text." for i in range(6)])
        paragraphs = "\n\n".join(["paragraph " + " ".join(["word"] * 50) + "." for _ in range(10)])
        content = headings + "\n\n" + paragraphs
        score = svc._score_completeness(content, {})
        assert score > 5.0

    def test_headings_add_to_score(self, svc):
        """Same word count with headings should beat content without headings."""
        words = " ".join(["word"] * 600)
        no_headings = words
        with_headings = "## Intro\n\n" + words + "\n\n## Body\n\n" + words
        score_no = svc._score_completeness(no_headings, {})
        score_with = svc._score_completeness(with_headings, {})
        assert score_with > score_no

    def test_bullet_list_adds_score(self, svc):
        """Presence of a bullet list should bump completeness."""
        base = " ".join(["word"] * 300)
        with_list = base + "\n\n- item one\n- item two\n- item three"
        score_base = svc._score_completeness(base, {})
        score_list = svc._score_completeness(with_list, {})
        assert score_list > score_base

    def test_score_capped_at_10(self, svc):
        """Score must never exceed 10."""
        # Construct maximally complete content
        headings = "\n".join([f"## Section {i}\n\nContent here." for i in range(10)])
        body = "\n\n".join(["paragraph " + " ".join(["word"] * 60)] * 20)
        bullets = "\n".join([f"- item {i}" for i in range(10)])
        content = headings + "\n\n" + body + "\n\n" + bullets
        score = svc._score_completeness(content, {})
        assert score <= 10.0


# ---------------------------------------------------------------------------
# Truncation detection
# ---------------------------------------------------------------------------


class TestTruncationDetection:
    """Tests for detect_truncation static method."""

    def test_complete_content_not_flagged(self):
        """Content ending with proper punctuation should not be flagged."""
        assert not UnifiedQualityService.detect_truncation(
            "This is a complete article. It ends with a period."
        )

    def test_mid_sentence_flagged(self):
        """Content ending mid-sentence should be flagged as truncated."""
        assert UnifiedQualityService.detect_truncation(
            "This article discusses many important topics in the field of software engineering and distributed systems. "
            "The key insight from recent research is that modern architectures need to"
        )

    def test_ends_with_url_not_flagged(self):
        """Content ending with a URL (references section) should not be flagged."""
        assert not UnifiedQualityService.detect_truncation(
            "For more information see https://example.com/article"
        )

    def test_empty_content_not_flagged(self):
        """Empty or very short content should not be flagged."""
        assert not UnifiedQualityService.detect_truncation("")
        assert not UnifiedQualityService.detect_truncation("Short.")

    def test_html_content_truncated(self):
        """HTML content cut off mid-sentence should be detected."""
        assert UnifiedQualityService.detect_truncation(
            "<p>The system uses a distributed architecture.</p>"
            "<p>The key components include the load balancer, the"
        )

    def test_completeness_penalty_on_truncation(self, svc=None):
        """Truncated content should receive a completeness score penalty."""
        svc = UnifiedQualityService()
        complete = " ".join(["word"] * 549) + " final sentence."
        truncated = " ".join(["word"] * 549) + " this sentence never finishes and keeps going"
        score_complete = svc._score_completeness(complete, {})
        score_truncated = svc._score_completeness(truncated, {})
        assert (
            score_truncated < score_complete
        ), f"Truncated ({score_truncated}) should score lower than complete ({score_complete})"


# ---------------------------------------------------------------------------
# _score_relevance
# ---------------------------------------------------------------------------


class TestScoreRelevance:
    def test_no_topic_returns_neutral(self, svc):
        """When no topic is provided the function returns 6.0 (neutral)."""
        score = svc._score_relevance("Some content here.", {})
        assert score == pytest.approx(6.0)

    def test_on_topic_content_scores_high(self, svc):
        """Content that heavily covers the topic words scores above 7."""
        context = {"topic": "machine learning algorithms"}
        content = (
            "Machine learning algorithms are widely used. "
            "These algorithms rely on training data. "
            "Learning from patterns, machine learning has transformed technology."
        )
        score = svc._score_relevance(content, context)
        assert score >= 7.0

    def test_off_topic_content_scores_low(self, svc):
        """Content that never mentions topic words scores below 6."""
        context = {"topic": "quantum computing processors"}
        content = "I love baking bread on Sunday mornings with fresh ingredients."
        score = svc._score_relevance(content, context)
        assert score < 6.0

    def test_on_topic_beats_off_topic(self, svc):
        """On-topic content must score higher than off-topic content."""
        context = {"topic": "renewable energy solar panels"}
        on_topic = (
            "Renewable energy sources like solar panels are growing rapidly. "
            "Solar energy is a key renewable resource. Panels convert energy efficiently."
        )
        off_topic = "The football match ended in a draw last Saturday evening."
        score_on = svc._score_relevance(on_topic, context)
        score_off = svc._score_relevance(off_topic, context)
        assert score_on > score_off

    def test_keyword_stuffing_penalised(self, svc):
        """Excessive repetition of the topic phrase should be penalised (density cap)."""
        context = {"topic": "cheap loans"}
        # Repeat phrase > 5 times per 100 words — triggers stuffing cap
        content = " ".join(["cheap loans"] * 30)
        score = svc._score_relevance(content, context)
        assert score <= 5.5

    def test_primary_keyword_fallback(self, svc):
        """primary_keyword is used when topic is absent."""
        context = {"primary_keyword": "data science"}
        # Use enough words so density stays low, with topic words present
        content = (
            "Data science is a rapidly growing field. Scientists and engineers use "
            "statistics, programming, and domain expertise to extract insights. "
            "The science of data has transformed industries across the globe. "
            "Professionals in this field rely on algorithms and visualisations."
        )
        score = svc._score_relevance(content, context)
        # Coverage is 100% (both 'data' and 'science' present); density should stay low
        assert score > 5.0


# ---------------------------------------------------------------------------
# _score_readability
# ---------------------------------------------------------------------------


class TestScoreReadability:
    def test_empty_content_returns_midpoint(self, svc):
        """Empty string should not crash and returns the neutral 7.0."""
        score = svc._score_readability("")
        assert score == pytest.approx(7.0)

    def test_simple_short_sentences_score_high(self, svc):
        """Short, simple sentences produce a high Flesch Reading Ease score."""
        content = "The cat sat. The dog ran. Birds fly high. Sun is bright."
        score = svc._score_readability(content)
        assert score >= 7.0

    def test_run_on_sentences_score_lower_than_varied(self, svc):
        """A wall of text with a single long sentence should score lower than varied prose."""
        run_on = (
            "This extremely long and convoluted sentence contains many subordinate clauses "
            "and excessive punctuation-free stretches of text that make it very difficult to "
            "follow for any reader who has not already invested significant time and effort "
            "in understanding the complex and multi-layered subject matter being discussed here."
        )
        varied = (
            "Reading is fun. Short sentences help. They aid comprehension. "
            "Readers enjoy clear prose. Good writing uses variety."
        )
        score_run_on = svc._score_readability(run_on)
        score_varied = svc._score_readability(varied)
        assert score_varied > score_run_on

    def test_score_within_0_to_10(self, svc):
        """Score must always be clamped between 0 and 10."""
        for content in ["a", "x " * 500, "short."]:
            score = svc._score_readability(content)
            assert 0.0 <= score <= 10.0


# ---------------------------------------------------------------------------
# _score_engagement
# ---------------------------------------------------------------------------


class TestScoreEngagement:
    def test_plain_text_scores_at_baseline(self, svc):
        """Content with no engagement signals returns the 5.0 baseline."""
        content = "This is a plain sentence with no questions or lists."
        score = svc._score_engagement(content)
        assert score == pytest.approx(5.0, abs=1.1)

    def test_bullet_points_raise_score(self, svc):
        """Bullet list items should bump engagement."""
        content = "Here are the points:\n- First item\n- Second item\n- Third item"
        score = svc._score_engagement(content)
        assert score > 5.0

    def test_question_raises_score(self, svc):
        """A question mark should add engagement."""
        content = "Have you ever wondered why the sky is blue? It is fascinating."
        score = svc._score_engagement(content)
        assert score > 5.0

    def test_exclamation_raises_score(self, svc):
        """One or two exclamation marks bump engagement."""
        content = "This is amazing! The results exceeded all expectations."
        score = svc._score_engagement(content)
        assert score > 5.0

    def test_multiple_signals_stack(self, svc):
        """Content with bullets, questions, and varied paragraphs scores higher."""
        rich = (
            "Are you ready to learn?\n\n"
            "- Point one is important\n"
            "- Point two is critical\n\n"
            "This is fascinating! Great things happen."
        )
        plain = "This is some plain text without any special elements."
        assert svc._score_engagement(rich) > svc._score_engagement(plain)

    def test_score_capped_at_10(self, svc):
        """Score must never exceed 10."""
        content = (
            "Wow! Amazing! Great!\n\n"
            "- Item 1\n* Item 2\n\n"
            "Did you see this? Can you believe it? Really?\n\n"
            "Short paragraph.\n\nLonger paragraph with more content here indeed."
        )
        score = svc._score_engagement(content)
        assert score <= 10.0


# ---------------------------------------------------------------------------
# QualityDimensions.average — critical-floor capping
# ---------------------------------------------------------------------------


class TestQualityDimensionsAverage:
    def test_normal_average_when_all_dimensions_healthy(self):
        """All dimensions above floor: result is straightforward average."""
        dims = QualityDimensions(
            clarity=80,
            accuracy=70,
            completeness=70,
            relevance=75,
            seo_quality=65,
            readability=70,
            engagement=70,
        )
        expected = (80 + 70 + 70 + 75 + 65 + 70 + 70) / 7
        assert dims.average() == pytest.approx(expected, abs=0.01)

    def test_low_readability_does_not_cap_overall(self):
        """Readability below CRITICAL_FLOOR should NOT cap overall score (#1238).

        Flesch formula penalizes technical vocabulary unfairly, so readability
        was removed from critical dimensions. It still contributes to the
        average but does not trigger the hard cap.
        """
        dims = QualityDimensions(
            clarity=90,
            accuracy=90,
            completeness=90,
            relevance=90,
            seo_quality=90,
            readability=40,  # below floor — but NOT a critical dim
            engagement=90,
        )
        avg = dims.average()
        # Raw average: (90*6 + 40) / 7 ≈ 82.9 — not capped
        assert avg > 80.0, f"Readability should not cap score; got {avg}"

    def test_low_clarity_caps_overall(self):
        """Clarity below CRITICAL_FLOOR caps the overall score."""
        dims = QualityDimensions(
            clarity=45,  # below floor
            accuracy=90,
            completeness=90,
            relevance=90,
            seo_quality=90,
            readability=90,
            engagement=90,
        )
        assert dims.average() <= 45.0

    def test_low_relevance_caps_overall(self):
        """Relevance below CRITICAL_FLOOR caps the overall score."""
        dims = QualityDimensions(
            clarity=90,
            accuracy=90,
            completeness=90,
            relevance=30,  # below floor
            seo_quality=90,
            readability=90,
            engagement=90,
        )
        assert dims.average() <= 30.0

    def test_non_critical_dimension_below_floor_does_not_cap(self):
        """accuracy/completeness/seo_quality below floor do NOT cap overall score."""
        dims = QualityDimensions(
            clarity=80,
            accuracy=20,  # non-critical — should NOT cap
            completeness=80,
            relevance=80,
            seo_quality=80,
            readability=80,
            engagement=80,
        )
        avg = dims.average()
        # Average is > 50, so no cap should apply from accuracy
        assert avg > 50.0


# ---------------------------------------------------------------------------
# evaluate() — public API orchestration
# ---------------------------------------------------------------------------


from unittest.mock import AsyncMock, MagicMock

from services.quality_service import (
    EvaluationMethod,
    QualityAssessment,
    get_content_quality_service,
    get_quality_service,
)


class TestEvaluatePublicAPI:
    @pytest.mark.asyncio
    async def test_pattern_based_returns_assessment(self, svc):
        content = (
            "Building software well takes practice. The best engineers learn from "
            "every mistake and refactor relentlessly. According to research [1], "
            "developers who write tests have fewer bugs in production. See more at "
            "https://example.com/testing for the full study. Testing matters."
        )
        result = await svc.evaluate(content, context={"topic": "testing"}, store_result=False)
        assert isinstance(result, QualityAssessment)
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED
        assert 0 <= result.overall_score <= 100
        assert result.word_count is not None
        assert result.word_count > 10
        assert result.content_length == len(content)

    @pytest.mark.asyncio
    async def test_llm_based_with_no_client_falls_back_to_pattern(self, svc):
        """LLM_BASED method without llm_client should fall back to pattern-based."""
        result = await svc.evaluate(
            "Some plain content for testing.",
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        # The fallback path returns an assessment marked as PATTERN_BASED
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED

    @pytest.mark.asyncio
    async def test_evaluate_updates_statistics(self, svc):
        before = svc.get_statistics()
        assert before["total_evaluations"] == 0
        await svc.evaluate("First content sample.", store_result=False)
        await svc.evaluate("Second content sample.", store_result=False)
        after = svc.get_statistics()
        assert after["total_evaluations"] == 2
        assert after["passing_count"] + after["failing_count"] == 2

    @pytest.mark.asyncio
    async def test_evaluate_running_average(self, svc):
        await svc.evaluate("Sample one.", store_result=False)
        await svc.evaluate("Sample two.", store_result=False)
        stats = svc.get_statistics()
        assert stats["average_score"] >= 0
        assert stats["average_score"] <= 100

    @pytest.mark.asyncio
    async def test_default_method_is_pattern_based(self, svc):
        result = await svc.evaluate("Default eval method test.", store_result=False)
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED

    @pytest.mark.asyncio
    async def test_unknown_method_falls_back_to_pattern(self, svc):
        # Pass a non-enum value to hit the else branch in evaluate()
        class _Fake:
            value = "fake"
        result = await svc.evaluate("test", method=_Fake(), store_result=False)
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED


class TestEvaluateLLMPath:
    @pytest.mark.asyncio
    async def test_llm_returns_valid_json(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=(
            '{"clarity": 8, "accuracy": 9, "completeness": 7, "relevance": 8, '
            '"seo_quality": 7, "readability": 8, "engagement": 9, '
            '"feedback": "Good post overall.", "suggestions": ["add more examples"]}'
        ))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "Test content.",
            context={"topic": "x"},
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        assert result.evaluation_method == EvaluationMethod.LLM_BASED
        assert result.feedback == "Good post overall."
        assert result.dimensions.clarity == 80  # 8 * 10
        assert result.dimensions.accuracy == 90

    @pytest.mark.asyncio
    async def test_llm_returns_clamped_scores(self):
        """Out-of-range scores should be clamped to 0-10 then scaled to 0-100."""
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=(
            '{"clarity": 15, "accuracy": -3, "completeness": 7, "relevance": 7, '
            '"seo_quality": 7, "readability": 7, "engagement": 7}'
        ))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "x",
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        # 15 clamped to 10 -> 100
        assert result.dimensions.clarity == 100
        # -3 clamped to 0 -> 0
        assert result.dimensions.accuracy == 0

    @pytest.mark.asyncio
    async def test_llm_invalid_score_uses_neutral_fallback(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=(
            '{"clarity": "not a number", "accuracy": 8, "completeness": 7, '
            '"relevance": 7, "seo_quality": 7, "readability": 7, "engagement": 7}'
        ))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "x",
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        # Non-numeric -> 50.0 neutral fallback
        assert result.dimensions.clarity == 50

    @pytest.mark.asyncio
    async def test_llm_no_json_falls_back_to_pattern(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value="just some text without any JSON")
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "Plain content.",
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        # Fell back to pattern-based
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED

    @pytest.mark.asyncio
    async def test_llm_call_raises_falls_back_to_pattern(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(side_effect=RuntimeError("ollama unreachable"))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "Plain content.",
            method=EvaluationMethod.LLM_BASED,
            store_result=False,
        )
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED


class TestEvaluateHybridPath:
    @pytest.mark.asyncio
    async def test_hybrid_with_no_llm_returns_pattern_based(self, svc):
        result = await svc.evaluate(
            "Hybrid test content.",
            method=EvaluationMethod.HYBRID,
            store_result=False,
        )
        # No LLM client → pattern-based assessment is returned directly
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED

    @pytest.mark.asyncio
    async def test_hybrid_averages_pattern_and_llm(self):
        llm = AsyncMock()
        llm.generate_text = AsyncMock(return_value=(
            '{"clarity": 10, "accuracy": 10, "completeness": 10, "relevance": 10, '
            '"seo_quality": 10, "readability": 10, "engagement": 10}'
        ))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "Hybrid combined evaluation content for testing.",
            method=EvaluationMethod.HYBRID,
            store_result=False,
        )
        assert result.evaluation_method == EvaluationMethod.HYBRID
        # LLM gave perfect 100s; combined should be > pattern alone but <= 100
        assert result.overall_score <= 100

    @pytest.mark.asyncio
    async def test_hybrid_with_llm_fallback_returns_pattern(self):
        """If LLM fails inside hybrid, the pattern-based result is returned (not HYBRID)."""
        llm = AsyncMock()
        llm.generate_text = AsyncMock(side_effect=Exception("oops"))
        svc = UnifiedQualityService(llm_client=llm)
        result = await svc.evaluate(
            "x",
            method=EvaluationMethod.HYBRID,
            store_result=False,
        )
        assert result.evaluation_method == EvaluationMethod.PATTERN_BASED


class TestEvaluateErrorPath:
    @pytest.mark.asyncio
    async def test_internal_exception_returns_minimal_assessment(self, svc, monkeypatch):
        """If pattern eval raises, evaluate() returns a 5.0 minimal assessment."""
        async def boom(*args, **kwargs):
            raise RuntimeError("scoring exploded")

        monkeypatch.setattr(svc, "_evaluate_pattern_based", boom)

        result = await svc.evaluate("x", store_result=False)
        assert result.overall_score == 5.0
        assert result.passing is False
        assert "Evaluation error" in result.feedback
        assert result.evaluated_by == "UnifiedQualityService-Error"


class TestStoreEvaluation:
    @pytest.mark.asyncio
    async def test_no_database_service_skips_persistence(self):
        svc = UnifiedQualityService(database_service=None)
        await svc.evaluate("x", store_result=True)  # should not raise

    @pytest.mark.asyncio
    async def test_no_task_id_in_context_skips_persistence(self):
        db = AsyncMock()
        db.create_quality_evaluation = AsyncMock()
        svc = UnifiedQualityService(database_service=db)
        await svc.evaluate("x", context={}, store_result=True)
        db.create_quality_evaluation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_task_id_persists(self):
        db = AsyncMock()
        db.create_quality_evaluation = AsyncMock()
        svc = UnifiedQualityService(database_service=db)
        await svc.evaluate(
            "Sample content for persistence test.",
            context={"task_id": "task-uuid-123", "topic": "x"},
            store_result=True,
        )
        db.create_quality_evaluation.assert_awaited_once()
        args, _ = db.create_quality_evaluation.call_args
        payload = args[0]
        assert payload["task_id"] == "task-uuid-123"
        assert payload["content_id"] == "task-uuid-123"
        assert "criteria" in payload
        assert "overall_score" in payload

    @pytest.mark.asyncio
    async def test_falls_back_to_content_id(self):
        db = AsyncMock()
        db.create_quality_evaluation = AsyncMock()
        svc = UnifiedQualityService(database_service=db)
        await svc.evaluate(
            "x",
            context={"content_id": "content-456"},
            store_result=True,
        )
        db.create_quality_evaluation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_failure_does_not_propagate(self):
        db = AsyncMock()
        db.create_quality_evaluation = AsyncMock(side_effect=RuntimeError("db down"))
        svc = UnifiedQualityService(database_service=db)
        # Should swallow the exception, not raise
        result = await svc.evaluate(
            "x",
            context={"task_id": "t1"},
            store_result=True,
        )
        assert isinstance(result, QualityAssessment)


class TestStatistics:
    def test_initial_statistics(self, svc):
        stats = svc.get_statistics()
        assert stats["total_evaluations"] == 0
        assert stats["passing_count"] == 0
        assert stats["failing_count"] == 0
        assert stats["pass_rate"] == 0
        assert stats["average_score"] == 0.0

    def test_pass_rate_with_no_evaluations(self, svc):
        # Division-by-zero protection
        assert svc.get_statistics()["pass_rate"] == 0


class TestFactoryFunctions:
    def test_get_quality_service_returns_instance(self):
        svc = get_quality_service()
        assert isinstance(svc, UnifiedQualityService)

    def test_get_content_quality_service_alias(self):
        svc = get_content_quality_service()
        assert isinstance(svc, UnifiedQualityService)

    def test_factory_passes_dependencies(self):
        db = MagicMock()
        llm = MagicMock()
        svc = get_quality_service(database_service=db, llm_client=llm)
        assert svc.database_service is db
        assert svc.llm_client is llm


# ---------------------------------------------------------------------------
# _detect_artifacts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectArtifacts:
    def test_no_artifacts_returns_empty(self):
        from services.quality_service import UnifiedQualityService
        clean = "# Real Article\n\nThis is a clean article about Python programming."
        assert UnifiedQualityService._detect_artifacts(clean) == []

    def test_photo_attribution_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "# Post\n\n*Photo by John Doe on Pexels*\n\nContent here."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Photo metadata" in a for a in artifacts)

    def test_image_credit_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "# Post\n\nImage credit: Shutterstock\n\nContent."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Photo metadata" in a for a in artifacts)

    def test_sdxl_leak_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "Generate with stable diffusion. negative prompt: ugly, low quality."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("image generation" in a for a in artifacts)

    def test_cinematic_image_prompt_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "cinematic lighting, no people, no text in the scene."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("image generation" in a for a in artifacts)

    def test_image_placeholder_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "Intro text.\n\n[IMAGE-1: A futuristic city]\n\nMore text."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Unresolved placeholders" in a for a in artifacts)

    def test_todo_placeholder_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "Real content. [TODO: add citation]. More content."
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Unresolved placeholders" in a for a in artifacts)

    def test_tbd_placeholder_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "Pricing: [TBD]"
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Unresolved placeholders" in a for a in artifacts)

    def test_raw_html_entity_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "A &amp; B &lt; C &gt; D"
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Raw HTML" in a for a in artifacts)

    def test_br_tag_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "Line 1<br/>Line 2<br>"
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Raw HTML" in a for a in artifacts)

    def test_empty_sections_detected(self):
        from services.quality_service import UnifiedQualityService
        content = "# Section A\n## Section B\n## Section C\n"
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Empty sections" in a for a in artifacts)

    def test_duplicate_sentences_detected(self):
        from services.quality_service import UnifiedQualityService
        # Both instances must match after the split-and-strip that _detect_artifacts
        # performs — so pad the first with leading context that will get split off.
        sentence = "Docker is a containerization platform that makes deployment easy"
        content = (
            f"Intro text here to get past the split boundary. "
            f"{sentence}. {sentence}. Some new content here."
        )
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert any("Duplicate sentences" in a for a in artifacts)

    def test_multiple_artifacts_stacked(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "*Photo by Jane on Unsplash*\n\n"
            "[IMAGE-1: hero]\n\n"
            "Content &amp; more content\n"
        )
        artifacts = UnifiedQualityService._detect_artifacts(content)
        assert len(artifacts) >= 3


# ---------------------------------------------------------------------------
# _score_llm_patterns — LLM slop detection
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreLLMPatterns:
    def test_clean_content_zero_or_minimal_penalty(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Building a FastAPI Service\n\n"
            "FastAPI gives you a typed, async web framework in under 100 lines. "
            "Start with a basic main.py and add endpoints as needed. "
            "For dependency injection, use Depends from fastapi.\n\n"
            "## Database setup\n\n"
            "Use asyncpg for Postgres. It's faster than SQLAlchemy for pure CRUD.\n\n"
            "## Deployment\n\n"
            "Docker image size matters. Use python:3.12-slim as the base."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert penalty <= 0
        assert penalty >= -2.0

    def test_cliche_opener_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# AI Development\n\n"
            "In today's digital landscape, AI is transforming everything. "
            "Real content here."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("opener" in i.lower() for i in issues)
        assert penalty < 0

    def test_heavy_buzzwords_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "Leverage cutting-edge synergy to harness innovative paradigm shifts. "
            "Our robust, seamless, game-changing solution will revolutionize your workflow. "
            "This transformative, disruptive technology is truly next-generation."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("buzzword" in i.lower() for i in issues)
        assert penalty < -1.0

    def test_filler_phrases_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "It's important to note that when it comes to databases, "
            "it should be mentioned that needless to say, "
            "the bottom line is at the end of the day, you need a DB."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("filler" in i.lower() for i in issues)

    def test_generic_transitions_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "Real content about Python.\n\n"
            "In conclusion, Python is great.\n\n"
            "To summarize, use Python.\n\n"
            "Final thoughts: Python wins.\n"
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("transition" in i.lower() for i in issues)

    def test_repetitive_starters_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "The system is fast. The system is reliable. The system is scalable. "
            "The system is secure. The system is maintained. The system is documented. "
            "The system runs nightly."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("repetitive" in i.lower() for i in issues)

    def test_listicle_title_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = "# 10 Ways to Speed Up Your Python Code\n\nReal content."
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("listicle" in i.lower() or "guide" in i.lower() for i in issues)

    def test_ultimate_guide_title_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = "# The Ultimate Guide to Docker\n\nReal content."
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("listicle" in i.lower() or "guide" in i.lower() for i in issues)

    def test_exclamation_spam_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = "# Post\n\nThis is amazing! Really cool! Totally! Awesome! Incredible! Wow!"
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("exclamation" in i.lower() for i in issues)

    def test_over_hedging_penalized(self):
        from services.quality_service import UnifiedQualityService
        content = (
            "# Post\n\n"
            "Python might be potentially useful and perhaps arguably could "
            "possibly may be somewhat useful."
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("hedg" in i.lower() for i in issues)

    def test_formulaic_structure_penalized(self):
        from services.quality_service import UnifiedQualityService
        section = "word " * 60
        content = (
            "# Post\n\n"
            f"## Section A\n{section}\n\n"
            f"## Section B\n{section}\n\n"
            f"## Section C\n{section}\n\n"
            f"## Section D\n{section}"
        )
        penalty, issues = UnifiedQualityService._score_llm_patterns(content)
        assert any("formulaic" in i.lower() for i in issues)

    def test_penalty_returns_tuple_of_two(self):
        from services.quality_service import UnifiedQualityService
        result = UnifiedQualityService._score_llm_patterns("# Post\n\nClean content.")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_penalty_is_float(self):
        from services.quality_service import UnifiedQualityService
        penalty, _ = UnifiedQualityService._score_llm_patterns("# Post\n\nClean.")
        assert isinstance(penalty, float)

    def test_issues_is_list_of_strings(self):
        from services.quality_service import UnifiedQualityService
        content = "# Top 10 Ways to Leverage Synergy\n\nIn today's digital landscape."
        _, issues = UnifiedQualityService._score_llm_patterns(content)
        assert isinstance(issues, list)
        assert all(isinstance(i, str) for i in issues)
