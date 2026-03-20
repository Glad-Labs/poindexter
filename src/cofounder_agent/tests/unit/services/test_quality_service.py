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
        assert score == pytest.approx(6.0, abs=0.5)

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
        content = " ".join(["word"] * 550)
        score = svc._score_completeness(content, {})
        assert 3.0 <= score <= 7.0

    def test_long_content_with_headings_scores_high(self, svc):
        """2000+ words with multiple headings should approach maximum."""
        headings = "\n".join([f"## Section {i}\n\nSome text." for i in range(6)])
        paragraphs = "\n\n".join(["paragraph " + " ".join(["word"] * 50)] * 10)
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
        """Empty string should not crash and returns the neutral 5.0."""
        score = svc._score_readability("")
        assert score == pytest.approx(5.0)

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

    def test_low_readability_caps_overall(self):
        """Readability below CRITICAL_FLOOR (50) should cap the overall score."""
        dims = QualityDimensions(
            clarity=90,
            accuracy=90,
            completeness=90,
            relevance=90,
            seo_quality=90,
            readability=40,  # below floor
            engagement=90,
        )
        avg = dims.average()
        assert avg <= 40.0  # capped at the critical dimension's value

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
