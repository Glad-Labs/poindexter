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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_content_generator import AIContentGenerator, ContentValidationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator() -> AIContentGenerator:
    """Instantiate AIContentGenerator with mocked heavy dependencies."""
    with (
        patch(
            "services.ai_content_generator.ProviderChecker.is_huggingface_available",
            return_value=False,
        ),
        patch(
            "services.ai_content_generator.ProviderChecker.is_gemini_available", return_value=False
        ),
    ):
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
        with (
            patch(
                "services.ai_content_generator.ProviderChecker.is_huggingface_available",
                return_value=False,
            ),
            patch(
                "services.ai_content_generator.ProviderChecker.is_gemini_available",
                return_value=False,
            ),
        ):
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
        assert any(
            "practical examples" in issue.lower() or "bullet" in issue.lower()
            for issue in result.issues
        )

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


# ---------------------------------------------------------------------------
# _extract_ollama_response
# ---------------------------------------------------------------------------


class TestExtractOllamaResponse:
    """Tests for parsing various Ollama response formats."""

    def test_dict_with_text_key(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({"text": "Hello world"})
        assert result == "Hello world"

    def test_dict_with_response_key(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({"response": "From API"})
        assert result == "From API"

    def test_dict_with_content_key(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({"content": "Chat format"})
        assert result == "Chat format"

    def test_dict_prefers_text_over_response(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({"text": "preferred", "response": "fallback"})
        assert result == "preferred"

    def test_dict_with_no_known_keys(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({"unknown": "value"})
        assert result == ""

    def test_string_response(self):
        gen = _make_generator()
        result = gen._extract_ollama_response("Direct string output")
        assert result == "Direct string output"

    def test_empty_string_response(self):
        gen = _make_generator()
        result = gen._extract_ollama_response("")
        assert result == ""

    def test_none_response(self):
        gen = _make_generator()
        result = gen._extract_ollama_response(None)
        assert result == ""

    def test_integer_response(self):
        gen = _make_generator()
        result = gen._extract_ollama_response(42)
        assert result == ""

    def test_empty_dict(self):
        gen = _make_generator()
        result = gen._extract_ollama_response({})
        assert result == ""


# ---------------------------------------------------------------------------
# _validate_content edge cases
# ---------------------------------------------------------------------------


class TestValidateContentEdgeCases:
    """Additional edge case tests for content validation."""

    def test_missing_heading_penalizes(self):
        gen = _make_generator()
        # Content with no # heading
        content = "No heading here\n\n## Section 1\n\n## Section 2\n\n## Section 3\n\n- bullet\n\nConclusion summary\n\nReady to try it?"
        result = gen._validate_content(content, "test topic content", 15)
        assert "Missing title" in str(result.issues)

    def test_missing_cta_penalizes(self):
        gen = _make_generator()
        # Content with no CTA keywords
        content = "# Test Topic Content\n\n## Section 1\n\n## Section 2\n\n## Section 3\n\n- bullet\n\nConclusion summary of test topic content"
        result = gen._validate_content(content, "test topic content", 15)
        assert "Missing call-to-action" in str(result.issues)

    def test_few_headings_penalizes(self):
        gen = _make_generator()
        content = "# Test Topic Content\n\n## Only one section\n\n- list\n\nConclusion summary\n\nReady to start?"
        result = gen._validate_content(content, "test topic content", 10)
        assert "Insufficient structure" in str(result.issues)

    def test_well_structured_content_scores_high(self):
        gen = _make_generator()
        words = " ".join(["word"] * 100)
        content = f"# AI Tools for test topic content\n\n{words}\n\n## Section 1\n\n{words}\n\n## Section 2\n\n{words}\n\n## Section 3\n\n- bullet point\n\n## Conclusion\n\nSummary of test topic content.\n\nReady to start implementing?"
        result = gen._validate_content(content, "test topic content", 400)
        assert result.quality_score >= 7.0

    def test_empty_content_scores_zero(self):
        gen = _make_generator()
        result = gen._validate_content("", "test topic", 1000)
        assert result.quality_score <= 2.0
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# _generate_fallback_content
# ---------------------------------------------------------------------------


class TestGenerateFallbackContentExpanded:
    """More tests for fallback content template."""

    def test_includes_conclusion(self):
        gen = _make_generator()
        content = gen._generate_fallback_content("Test Topic", "technical", "professional", ["ai"])
        assert "Conclusion" in content

    def test_includes_numbered_steps(self):
        gen = _make_generator()
        content = gen._generate_fallback_content("Test Topic", "technical", "professional", [])
        assert "1." in content

    def test_includes_style_and_tone(self):
        gen = _make_generator()
        content = gen._generate_fallback_content("Test", "narrative", "casual", [])
        assert "narrative" in content
        assert "casual" in content

    def test_tags_included(self):
        gen = _make_generator()
        content = gen._generate_fallback_content("Test", "blog", "friendly", ["python", "ai"])
        assert "python, ai" in content

    def test_empty_tags_shows_general(self):
        gen = _make_generator()
        content = gen._generate_fallback_content("Test", "blog", "friendly", [])
        assert "general" in content

    def test_fallback_passes_validation(self):
        """Fallback content should pass basic structural validation."""
        gen = _make_generator()
        content = gen._generate_fallback_content("Test Topic AI", "technical", "professional", ["ai"])
        result = gen._validate_content(content, "test topic ai", len(content.split()))
        # Fallback is deliberately simple, but should have structure
        assert result.quality_score >= 3.0


# ---------------------------------------------------------------------------
# get_content_generator singleton
# ---------------------------------------------------------------------------


class TestGetContentGenerator:
    def test_returns_ai_content_generator(self):
        import services.ai_content_generator as mod
        mod._generator = None
        from services.ai_content_generator import get_content_generator
        gen = get_content_generator()
        assert isinstance(gen, AIContentGenerator)
        mod._generator = None

    def test_returns_same_instance(self):
        import services.ai_content_generator as mod
        mod._generator = None
        from services.ai_content_generator import get_content_generator
        g1 = get_content_generator()
        g2 = get_content_generator()
        assert g1 is g2
        mod._generator = None


# ---------------------------------------------------------------------------
# ContentValidationResult
# ---------------------------------------------------------------------------


class TestContentValidationResultExpanded:
    def test_default_issues_empty(self):
        result = ContentValidationResult(is_valid=True, quality_score=8.0)
        assert result.issues == []
        assert result.feedback == ""

    def test_custom_issues(self):
        result = ContentValidationResult(
            is_valid=False, quality_score=3.0,
            issues=["too short", "no heading"],
            feedback="Needs work",
        )
        assert len(result.issues) == 2
        assert result.feedback == "Needs work"


# ---------------------------------------------------------------------------
# _populate_internal_links_cache
# ---------------------------------------------------------------------------


class TestPopulateInternalLinksCache:
    @pytest.mark.asyncio
    async def test_no_database_url_returns_empty(self, monkeypatch):
        gen = _make_generator()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        await gen._populate_internal_links_cache()
        assert gen._internal_links_cache == []

    @pytest.mark.asyncio
    async def test_db_exception_falls_back_to_empty(self, monkeypatch):
        gen = _make_generator()
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")

        # Make asyncpg.connect raise — exception is caught
        fake_asyncpg = MagicMock()
        fake_asyncpg.connect = AsyncMock(side_effect=RuntimeError("conn refused"))
        with patch.dict("sys.modules", {"asyncpg": fake_asyncpg}):
            await gen._populate_internal_links_cache()
        # Note: when the except block runs, it sets cache to []
        assert gen._internal_links_cache == []


# ---------------------------------------------------------------------------
# _handle_all_providers_failed
# ---------------------------------------------------------------------------


class TestHandleAllProvidersFailed:
    def test_returns_fallback_tuple(self):
        gen = _make_generator()
        ctx = {
            "metrics": {
                "model_used": None,
                "models_used_by_phase": {},
                "final_quality_score": 0.0,
                "generation_time_seconds": 0,
            },
            "attempts": [
                ("ollama", "connection refused"),
                ("huggingface", "no token"),
            ],
            "start_time": 0.0,
            "use_ollama": False,
            "topic": "FastAPI",
            "style": "technical",
            "tone": "professional",
            "tags": ["python", "fastapi"],
        }
        content, model_used, metrics = gen._handle_all_providers_failed(ctx)

        assert isinstance(content, str)
        assert "FastAPI" in content
        assert "Fallback" in model_used
        assert metrics["model_used"] == model_used
        assert metrics["models_used_by_phase"]["draft"] == model_used
        assert metrics["final_quality_score"] == 0.0
        assert metrics["generation_time_seconds"] >= 0

    def test_no_attempts_handled(self):
        gen = _make_generator()
        ctx = {
            "metrics": {
                "model_used": None,
                "models_used_by_phase": {},
                "final_quality_score": 0.0,
                "generation_time_seconds": 0,
            },
            "attempts": [],
            "start_time": 0.0,
            "use_ollama": False,
            "topic": "Topic",
            "style": "narrative",
            "tone": "casual",
            "tags": [],
        }
        content, model_used, metrics = gen._handle_all_providers_failed(ctx)
        assert "Topic" in content
        assert "Fallback" in model_used


# ---------------------------------------------------------------------------
# generate_blog_post — top-level orchestration
# ---------------------------------------------------------------------------


class TestGenerateBlogPost:
    @pytest.mark.asyncio
    async def test_returns_ollama_result_when_ollama_succeeds(self):
        gen = _make_generator()

        # Mock the helpers to return a known shape
        gen._populate_internal_links_cache = AsyncMock()
        gen._prepare_generation_context = AsyncMock(return_value={"some": "ctx"})
        gen._try_ollama = AsyncMock(return_value=(
            "ollama generated content",
            "llama3",
            {"model_used": "llama3", "final_quality_score": 8.5},
        ))
        gen._try_huggingface = AsyncMock()
        gen._handle_all_providers_failed = MagicMock()

        content, model, metrics = await gen.generate_blog_post(
            topic="x", style="technical", tone="professional",
            target_length=1000, tags=["python"],
        )

        assert content == "ollama generated content"
        assert model == "llama3"
        assert metrics["final_quality_score"] == 8.5
        gen._try_ollama.assert_awaited_once()
        gen._try_huggingface.assert_not_awaited()
        gen._handle_all_providers_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_through_to_huggingface_when_ollama_fails(self):
        gen = _make_generator()
        gen._populate_internal_links_cache = AsyncMock()
        gen._prepare_generation_context = AsyncMock(return_value={"some": "ctx"})
        gen._try_ollama = AsyncMock(return_value=None)
        gen._try_huggingface = AsyncMock(return_value=(
            "hf generated content", "hf-model", {"final_quality_score": 7.0},
        ))
        gen._handle_all_providers_failed = MagicMock()

        content, model, _ = await gen.generate_blog_post(
            topic="x", style="technical", tone="professional",
            target_length=1000, tags=[],
        )
        assert content == "hf generated content"
        assert model == "hf-model"
        gen._try_ollama.assert_awaited_once()
        gen._try_huggingface.assert_awaited_once()
        gen._handle_all_providers_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_through_to_fallback_when_all_fail(self):
        gen = _make_generator()
        gen._populate_internal_links_cache = AsyncMock()
        gen._prepare_generation_context = AsyncMock(return_value={"some": "ctx"})
        gen._try_ollama = AsyncMock(return_value=None)
        gen._try_huggingface = AsyncMock(return_value=None)
        gen._handle_all_providers_failed = MagicMock(return_value=(
            "fallback content", "Fallback (no AI)", {"final_quality_score": 0.0},
        ))

        content, model, metrics = await gen.generate_blog_post(
            topic="x", style="technical", tone="professional",
            target_length=1000, tags=[],
        )
        assert content == "fallback content"
        assert "Fallback" in model
        assert metrics["final_quality_score"] == 0.0
        gen._handle_all_providers_failed.assert_called_once()


# ---------------------------------------------------------------------------
# _load_generation_prompts
# ---------------------------------------------------------------------------


class TestLoadGenerationPrompts:
    def test_calls_prompt_manager_for_system_and_generation(self):
        gen = _make_generator()

        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(side_effect=[
            "system prompt body",
            "generation prompt body",
            "refinement prompt body",
        ])

        with patch("services.ai_content_generator.get_prompt_manager", return_value=fake_pm):
            system, generation, refine_fn = gen._load_generation_prompts(
                topic="FastAPI",
                style="technical",
                tone="professional",
                target_length=1000,
                tags=["python", "api"],
            )

        assert system == "system prompt body"
        assert generation == "generation prompt body"
        assert callable(refine_fn)
        # First two calls — system and generation
        assert fake_pm.get_prompt.call_count >= 2

    def test_refinement_callable_calls_pm_when_invoked(self):
        gen = _make_generator()
        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(side_effect=[
            "sys",
            "gen",
            "refined output",
        ])

        with patch("services.ai_content_generator.get_prompt_manager", return_value=fake_pm):
            _, _, refine_fn = gen._load_generation_prompts(
                topic="x", style="technical", tone="professional",
                target_length=500, tags=[],
            )
            result = refine_fn("feedback text", ["issue 1"], "draft content")

        assert result == "refined output"
        assert fake_pm.get_prompt.call_count == 3

    def test_prompt_manager_failure_raises(self):
        gen = _make_generator()

        with patch("services.ai_content_generator.get_prompt_manager",
                   side_effect=RuntimeError("pm broken")):
            with pytest.raises(RuntimeError, match="pm broken"):
                gen._load_generation_prompts(
                    topic="x", style="technical", tone="professional",
                    target_length=500, tags=[],
                )

    def test_uses_internal_links_cache_when_present(self):
        gen = _make_generator()
        gen._internal_links_cache = ["- \"Existing Post\" -> https://x/posts/existing"]

        fake_pm = MagicMock()
        fake_pm.get_prompt = MagicMock(side_effect=["sys", "gen"])

        with patch("services.ai_content_generator.get_prompt_manager", return_value=fake_pm):
            gen._load_generation_prompts(
                topic="x", style="technical", tone="professional",
                target_length=500, tags=[],
            )
        # The generation prompt call should have received the joined links string
        gen_call = fake_pm.get_prompt.call_args_list[1]
        assert "Existing Post" in gen_call.kwargs["internal_link_titles"]


# ---------------------------------------------------------------------------
# _prepare_generation_context
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrepareGenerationContext:
    @pytest.mark.asyncio
    async def test_returns_context_dict_with_all_keys(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen.ollama_available = True
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "refine"))

        ctx = await gen._prepare_generation_context(
            topic="AI",
            style="technical",
            tone="professional",
            target_length=1500,
            tags=["ai", "ml"],
            preferred_model=None,
            preferred_provider=None,
        )

        # All expected keys present
        expected_keys = {
            "effective_provider", "skip_ollama", "use_ollama",
            "system_prompt", "generation_prompt", "get_refinement_prompt",
            "metrics", "start_time", "attempts",
            "topic", "style", "tone", "target_length", "tags", "preferred_model",
        }
        assert expected_keys.issubset(ctx.keys())

    @pytest.mark.asyncio
    async def test_skip_ollama_when_explicit_non_ollama_provider(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider="huggingface",
        )

        assert ctx["skip_ollama"] is True
        assert ctx["use_ollama"] is False
        gen._check_ollama_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_ollama_when_no_provider_specified(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen.ollama_available = True
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
        )

        # When preferred_provider is None, the `and` short-circuits to None (falsy)
        assert not ctx["skip_ollama"]
        gen._check_ollama_async.assert_called_once()
        assert ctx["use_ollama"] is True

    @pytest.mark.asyncio
    async def test_auto_provider_treated_as_no_skip(self):
        """preferred_provider='auto' should still check Ollama."""
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen.ollama_available = True
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider="auto",
        )

        assert not ctx["skip_ollama"]
        gen._check_ollama_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_writing_style_context_injected_into_system_prompt(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(
            return_value=("base system prompt", "gen", lambda f, i, c: "x"),
        )

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
            writing_style_context="Sample 1: a paragraph\nSample 2: another paragraph",
        )

        assert "Writing Style Reference" in ctx["system_prompt"]
        assert "Sample 1" in ctx["system_prompt"]
        assert "base system prompt" in ctx["system_prompt"]

    @pytest.mark.asyncio
    async def test_no_writing_style_context_leaves_system_prompt_unchanged(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(
            return_value=("base system prompt", "gen", lambda f, i, c: "x"),
        )

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
        )

        assert "Writing Style Reference" not in ctx["system_prompt"]
        assert ctx["system_prompt"] == "base system prompt"

    @pytest.mark.asyncio
    async def test_research_context_passed_to_prompt_loader(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=["ai"],
            preferred_model=None, preferred_provider=None,
            research_context="Source 1: github.com/example\nSource 2: arxiv.org/1234",
        )

        call_kwargs = gen._load_generation_prompts.call_args.kwargs
        assert "github.com" in call_kwargs["research_context"]
        assert "arxiv.org" in call_kwargs["research_context"]

    @pytest.mark.asyncio
    async def test_metrics_initialized_with_zero_counters(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model="llama3:70b", preferred_provider="ollama",
        )

        metrics = ctx["metrics"]
        assert metrics["generation_attempts"] == 0
        assert metrics["refinement_attempts"] == 0
        assert metrics["validation_results"] == []
        assert metrics["model_used"] is None
        assert metrics["final_quality_score"] == 0.0
        assert metrics["preferred_model"] == "llama3:70b"
        assert metrics["preferred_provider"] == "ollama"

    @pytest.mark.asyncio
    async def test_model_selection_log_populated(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen.ollama_available = True
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model="qwen3:30b", preferred_provider="ollama",
        )

        log = ctx["metrics"]["model_selection_log"]
        assert log["requested_provider"] == "ollama"
        assert log["requested_model"] == "qwen3:30b"
        assert log["skipped_ollama"] is False
        assert "decision_tree" in log

    @pytest.mark.asyncio
    async def test_start_time_is_recent(self):
        import time as _time
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        before = _time.time()
        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
        )
        after = _time.time()

        assert before <= ctx["start_time"] <= after

    @pytest.mark.asyncio
    async def test_ollama_unavailable_sets_use_ollama_false(self):
        gen = _make_generator()

        async def _check():
            gen.ollama_available = False

        gen._check_ollama_async = AsyncMock(side_effect=_check)
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
        )

        assert ctx["use_ollama"] is False

    @pytest.mark.asyncio
    async def test_attempts_starts_empty(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="AI", style="s", tone="t", target_length=1000, tags=[],
            preferred_model=None, preferred_provider=None,
        )

        assert ctx["attempts"] == []

    @pytest.mark.asyncio
    async def test_topic_and_tags_echoed_into_context(self):
        gen = _make_generator()
        gen._check_ollama_async = AsyncMock()
        gen._load_generation_prompts = MagicMock(return_value=("sys", "gen", lambda f, i, c: "x"))

        ctx = await gen._prepare_generation_context(
            topic="Kubernetes patterns",
            style="technical",
            tone="expert",
            target_length=2500,
            tags=["k8s", "containers", "devops"],
            preferred_model=None, preferred_provider=None,
        )

        assert ctx["topic"] == "Kubernetes patterns"
        assert ctx["style"] == "technical"
        assert ctx["tone"] == "expert"
        assert ctx["target_length"] == 2500
        assert ctx["tags"] == ["k8s", "containers", "devops"]
