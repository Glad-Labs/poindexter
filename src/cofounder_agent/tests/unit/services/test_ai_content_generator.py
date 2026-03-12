"""
Unit tests for services/ai_content_generator.py

Covers:
- ContentValidationResult: dataclass construction
- AIContentGenerator.__init__: default attributes
- AIContentGenerator._validate_content: quality scoring logic
  - Content at target length → high score
  - Content too short (critical: <70%) → score drops by 5
  - Content moderately short (70–90%) → score drops by 3
  - Content too long → minor score penalty
  - Missing conclusion → score drops by 1.5
  - Missing bullet points → score drops by 1.0
  - Topic relevance check
  - Score clamped to [0, 10]
- AIContentGenerator._generate_fallback_content: produces valid content
- AIContentGenerator._check_ollama_async: sets ollama_available / ollama_checked
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.ai_content_generator import AIContentGenerator, ContentValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator() -> AIContentGenerator:
    """Instantiate AIContentGenerator with mocked heavy dependencies."""
    with patch("services.ai_content_generator.ProviderChecker.is_huggingface_available", return_value=False), \
         patch("services.ai_content_generator.ProviderChecker.is_gemini_available", return_value=False):
        gen = AIContentGenerator(quality_threshold=7.5)
    return gen


def _good_content(topic: str = "Python programming", word_count: int = 1000) -> str:
    """Generate synthetic content that passes all quality checks."""
    # Start with a list (bullet points) and conclusion to satisfy checkers
    base = f"""# {topic}: A Comprehensive Guide

## Introduction to {topic}

{topic} is a foundational technology that every developer should understand deeply.
Here we start exploring the important concepts and practical applications.

## Key Concepts

When working with {topic}, consider the following:

- **Concept 1**: The first important concept
- **Concept 2**: Another critical idea
- **Concept 3**: A third perspective worth exploring
1. First practical step to implement
2. Second practical step

## Practical Applications

{topic} has many real-world applications in modern software development.
Understanding these helps practitioners build better systems.

## Conclusion: Key Takeaways

Ready to start applying {topic} in your projects?

Next steps: learn more about {topic} and begin implementing what you've read.
"""
    words = base.split()
    if len(words) < word_count:
        # Pad to target word count
        padding = f" {topic}" * (word_count - len(words) + 10)
        base += padding
    return base


# ---------------------------------------------------------------------------
# ContentValidationResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContentValidationResult:
    def test_construction(self):
        result = ContentValidationResult(
            is_valid=True, quality_score=8.5, issues=[], feedback="Great!"
        )
        assert result.is_valid is True
        assert result.quality_score == 8.5
        assert result.issues == []
        assert result.feedback == "Great!"

    def test_invalid_result(self):
        result = ContentValidationResult(
            is_valid=False,
            quality_score=4.0,
            issues=["Too short", "Missing conclusion"],
            feedback="Needs improvement",
        )
        assert result.is_valid is False
        assert len(result.issues) == 2


# ---------------------------------------------------------------------------
# AIContentGenerator initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAIContentGeneratorInit:
    def test_default_quality_threshold(self):
        gen = _make_generator()
        assert gen.quality_threshold == 7.5

    def test_custom_quality_threshold(self):
        with patch("services.ai_content_generator.ProviderChecker.is_huggingface_available", return_value=False), \
             patch("services.ai_content_generator.ProviderChecker.is_gemini_available", return_value=False):
            gen = AIContentGenerator(quality_threshold=6.0)
        assert gen.quality_threshold == 6.0

    def test_initial_state(self):
        gen = _make_generator()
        assert gen.ollama_available is False
        assert gen.ollama_checked is False
        assert gen.generation_attempts == 0
        assert gen.max_refinement_attempts == 3


# ---------------------------------------------------------------------------
# AIContentGenerator._validate_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateContent:
    """_validate_content quality scoring logic."""

    def test_good_content_at_target_length_passes(self):
        gen = _make_generator()
        content = _good_content(topic="Python programming", word_count=1000)
        result = gen._validate_content(content, "Python programming", 1000)
        assert result.is_valid is True
        assert result.quality_score >= gen.quality_threshold

    def test_critically_short_content_fails(self):
        """Content < 70% of target → big score penalty, should fail."""
        gen = _make_generator()
        # 300 words for 1000-word target = 30%, well below 70%
        short_content = "word " * 300
        result = gen._validate_content(short_content, "topic", 1000)
        assert result.is_valid is False
        # Should have a critical issue message
        assert any("CRITICAL" in issue for issue in result.issues)

    def test_moderately_short_content_penalizes_score(self):
        """Content between 70–90% of target → 3-point deduction."""
        gen = _make_generator()
        # 750 words for 1000-word target = 75%
        short_content = "word " * 750
        result = gen._validate_content(short_content, "word topic word", 1000)
        # Score should be penalized but not as severely as critical short
        assert any("too short" in issue.lower() for issue in result.issues)

    def test_too_long_content_minor_penalty(self):
        """Content > 110% of target → 1-point deduction."""
        gen = _make_generator()
        # 1200 words for 1000-word target = 120%
        long_content = "word " * 1200
        result = gen._validate_content(long_content, "word topic word", 1000)
        assert any("too long" in issue.lower() for issue in result.issues)

    def test_missing_conclusion_penalizes_score(self):
        """Content without conclusion keywords → 1.5-point deduction."""
        gen = _make_generator()
        # Content at target length but no conclusion keywords
        content = "word " * 1000  # no "conclusion", "summary", "takeaway", etc.
        result = gen._validate_content(content, "word topic word", 1000)
        assert any("conclusion" in issue.lower() for issue in result.issues)

    def test_missing_bullet_points_penalizes_score(self):
        """No bullet points or numbered lists → 1-point deduction."""
        gen = _make_generator()
        # Content without any bullets or lists
        content = "word " * 950 + " conclusion summary next "  # add conclusion keywords
        result = gen._validate_content(content, "word topic word", 1000)
        assert any("practical examples" in issue.lower() or "bullet" in issue.lower()
                   for issue in result.issues)

    def test_score_clamped_to_zero_minimum(self):
        """Very poor content should not produce a negative score."""
        gen = _make_generator()
        result = gen._validate_content("x", "nonexistent", 1000)
        assert result.quality_score >= 0

    def test_score_clamped_to_ten_maximum(self):
        """Score should never exceed 10."""
        gen = _make_generator()
        content = _good_content(topic="Python programming", word_count=1000)
        result = gen._validate_content(content, "Python programming", 1000)
        assert result.quality_score <= 10

    def test_returns_content_validation_result_instance(self):
        gen = _make_generator()
        content = _good_content(word_count=500)
        result = gen._validate_content(content, "topic", 500)
        assert isinstance(result, ContentValidationResult)

    def test_valid_flag_false_below_threshold(self):
        gen = _make_generator()
        # Severely short content should produce score < 7.5
        result = gen._validate_content("a b c", "topic", 1000)
        assert result.is_valid is False

    def test_feedback_includes_score(self):
        gen = _make_generator()
        content = _good_content(word_count=1000)
        result = gen._validate_content(content, "Python programming", 1000)
        assert "/10" in result.feedback

    def test_topic_relevance_check(self):
        """Content that doesn't mention topic words gets penalized."""
        gen = _make_generator()
        # Content with no topic words but otherwise good
        content = _good_content(topic="Python programming", word_count=1000)
        # Topic words = ["irrelevant", "unique", "keyword"]
        result = gen._validate_content(content, "irrelevant unique keyword", 1000)
        # Should flag topic relevance
        irrelevant_issues = [i for i in result.issues if "mentioned too few" in i.lower()]
        assert len(irrelevant_issues) > 0


# ---------------------------------------------------------------------------
# AIContentGenerator._generate_fallback_content
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateFallbackContent:
    """_generate_fallback_content produces valid Markdown content."""

    def test_returns_string(self):
        gen = _make_generator()
        result = gen._generate_fallback_content(
            topic="AI Trends",
            style="professional",
            tone="formal",
            tags=["ai", "technology"],
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_topic(self):
        gen = _make_generator()
        result = gen._generate_fallback_content(
            topic="Machine Learning", style="blog", tone="casual", tags=[]
        )
        assert "Machine Learning" in result

    def test_contains_bullet_points(self):
        gen = _make_generator()
        result = gen._generate_fallback_content(
            topic="Test Topic", style="blog", tone="casual", tags=[]
        )
        assert "- " in result or "* " in result or "1. " in result

    def test_contains_markdown_heading(self):
        gen = _make_generator()
        result = gen._generate_fallback_content(
            topic="Test Topic", style="blog", tone="casual", tags=[]
        )
        assert "# " in result

    def test_empty_tags_does_not_raise(self):
        gen = _make_generator()
        result = gen._generate_fallback_content(
            topic="Any Topic", style="blog", tone="neutral", tags=[]
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# AIContentGenerator._check_ollama_async
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckOllamaAsync:
    """_check_ollama_async sets ollama_available and ollama_checked."""

    @pytest.mark.asyncio
    async def test_ollama_available_when_returns_200(self):
        gen = _make_generator()
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("services.ai_content_generator.httpx.AsyncClient", return_value=mock_client):
            await gen._check_ollama_async()

        assert gen.ollama_available is True
        assert gen.ollama_checked is True

    @pytest.mark.asyncio
    async def test_ollama_unavailable_when_non_200(self):
        gen = _make_generator()
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("services.ai_content_generator.httpx.AsyncClient", return_value=mock_client):
            await gen._check_ollama_async()

        assert gen.ollama_available is False
        assert gen.ollama_checked is True

    @pytest.mark.asyncio
    async def test_ollama_unavailable_on_connection_error(self):
        gen = _make_generator()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=ConnectionRefusedError("Connection refused"))

        with patch("services.ai_content_generator.httpx.AsyncClient", return_value=mock_client):
            await gen._check_ollama_async()

        assert gen.ollama_available is False
        assert gen.ollama_checked is True

    @pytest.mark.asyncio
    async def test_check_not_repeated_when_already_checked(self):
        """Second call should be a no-op when ollama_checked=True."""
        gen = _make_generator()
        gen.ollama_checked = True
        gen.ollama_available = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock()

        with patch("services.ai_content_generator.httpx.AsyncClient", return_value=mock_client):
            await gen._check_ollama_async()

        # Should not have made any HTTP request
        mock_client.get.assert_not_awaited()
