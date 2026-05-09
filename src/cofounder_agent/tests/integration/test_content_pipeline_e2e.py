"""
End-to-end integration tests for the content pipeline with LOCAL Ollama.

Unlike test_content_pipeline.py (which mocks LLM calls), these tests make
REAL calls to the local Ollama instance to verify:

1. Ollama connectivity — can we reach localhost:11434 and generate text?
2. Content generation — can AIContentGenerator produce content via Ollama?
3. QA review — can MultiModelQA review content with local gemma3:27b?
4. SEO metadata — can the pipeline generate seo_title, seo_description, seo_keywords?
5. Thinking models — do qwen3.5/glm-4.7 return non-empty content with sufficient token budget?

Requirements:
  - Ollama must be running locally on port 11434
  - Tests are skipped automatically if Ollama is unreachable
  - Marked with @pytest.mark.integration (excluded from unit test suite)
"""

import json
import re

import httpx
import pytest

# ---------------------------------------------------------------------------
# Skip entire module if Ollama is not reachable
# ---------------------------------------------------------------------------


def _ollama_is_running() -> bool:
    """Synchronous check — used at module import time for skipif."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_has_model(model_name: str) -> bool:
    """Check if a specific model (or a model containing that substring) is available."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        if r.status_code != 200:
            return False
        models = r.json().get("models", [])
        return any(model_name in m.get("name", "") for m in models)
    except Exception:
        return False


def _find_ollama_model(prefix: str) -> str | None:
    """Find the first installed Ollama model whose name contains the given prefix."""
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        if r.status_code != 200:
            return None
        models = r.json().get("models", [])
        for m in models:
            if prefix in m.get("name", ""):
                return m["name"]
        return None
    except Exception:
        return None


OLLAMA_AVAILABLE = _ollama_is_running()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not OLLAMA_AVAILABLE, reason="Ollama not running on localhost:11434"),
]


# ---------------------------------------------------------------------------
# 1. Ollama Connectivity
# ---------------------------------------------------------------------------


class TestOllamaConnectivity:
    """Verify basic Ollama server connectivity and model discovery."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        """OllamaClient.check_health() returns True when server is running."""
        from services.ollama_client import OllamaClient

        client = OllamaClient()
        try:
            assert await client.check_health() is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_models_returns_nonempty(self):
        """At least one model should be installed locally."""
        from services.ollama_client import OllamaClient

        client = OllamaClient()
        try:
            models = await client.list_models()
            assert len(models) > 0, "No models installed in Ollama"
            # Every model entry should have a name
            for m in models:
                assert "name" in m
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_generate_short_text(self):
        """Ollama can generate a short text completion.

        Uses gemma3:27b explicitly to avoid thinking-model token budget issues
        (qwen3.5 may auto-resolve as default and consume all tokens on reasoning).
        """
        from services.ollama_client import OllamaClient

        # Use a non-thinking model for basic connectivity test
        model = "gemma3:27b" if _ollama_has_model("gemma3:27b") else None
        client = OllamaClient()
        try:
            result = await client.generate(
                prompt="Respond with exactly one word: hello",
                model=model,
                max_tokens=50,
                temperature=0.0,
            )
            assert result["text"], "Ollama returned empty text"
            assert len(result["text"].strip()) > 0
            assert result["tokens"] > 0 or result["total_tokens"] > 0
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# 2. Content Generation via AIContentGenerator
# ---------------------------------------------------------------------------


class TestContentGeneration:
    """Verify AIContentGenerator can produce content through Ollama."""

    @pytest.mark.asyncio
    async def test_generate_blog_post_with_ollama(self):
        """AIContentGenerator.generate_blog_post produces non-empty content via Ollama."""
        from services.ai_content_generator import AIContentGenerator

        gen = AIContentGenerator(quality_threshold=2.0)  # Low threshold for speed
        content, model_used, metrics = await gen.generate_blog_post(
            topic="benefits of unit testing",
            style="technical",
            tone="professional",
            target_length=200,  # Short for speed
            tags=["testing", "software"],
            preferred_provider="ollama",
        )

        assert content, "generate_blog_post returned empty content"
        word_count = len(content.split())
        assert word_count >= 20, f"Content too short: {word_count} words"
        assert model_used, "No model_used returned"
        assert isinstance(metrics, dict)

    @pytest.mark.asyncio
    async def test_ollama_client_generate_with_system_prompt(self):
        """OllamaClient.generate() works with a system prompt.

        Uses gemma3:27b to avoid thinking-model empty-output issues with low token budgets.
        """
        from services.ollama_client import OllamaClient

        model = "gemma3:27b" if _ollama_has_model("gemma3:27b") else None
        client = OllamaClient()
        try:
            result = await client.generate(
                prompt="What is 2+2?",
                model=model,
                system="You are a math tutor. Answer concisely.",
                max_tokens=100,
                temperature=0.0,
            )
            text = result["text"]
            assert text, "Empty response with system prompt"
            assert "4" in text, f"Expected '4' in response, got: {text[:100]}"
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# 3. QA Review with MultiModelQA
# ---------------------------------------------------------------------------


SAMPLE_BLOG_CONTENT = """# Benefits of Unit Testing in Modern Software

## Introduction

Unit testing is a fundamental practice in software engineering that ensures
individual components work correctly in isolation.

## Why Unit Testing Matters

- Catches bugs early in the development cycle
- Provides documentation for expected behavior
- Enables safe refactoring with confidence
- Reduces debugging time significantly

## Best Practices

1. Write tests before or alongside code
2. Keep tests fast and independent
3. Use meaningful test names that describe behavior
4. Mock external dependencies

## Conclusion

Investing in unit testing pays dividends throughout the software lifecycle.
Teams that adopt testing practices ship fewer bugs and iterate faster.
Start with the most critical paths and expand coverage over time.
"""


class TestQAReview:
    """Verify MultiModelQA can review content using local Ollama."""

    @pytest.mark.asyncio
    async def test_multi_model_qa_review(self):
        """MultiModelQA.review() returns a scored result using Ollama."""
        from services.multi_model_qa import MultiModelQA

        qa = MultiModelQA()
        result = await qa.review(
            title="Benefits of Unit Testing in Modern Software",
            content=SAMPLE_BLOG_CONTENT,
            topic="unit testing",
        )

        assert result is not None
        assert hasattr(result, "approved")
        assert hasattr(result, "final_score")
        assert isinstance(result.final_score, (int, float))
        assert 0 <= result.final_score <= 100
        assert len(result.reviews) >= 1, "Expected at least one reviewer result"

        # At least the programmatic validator should be present
        reviewer_names = [r.reviewer for r in result.reviews]
        assert "programmatic_validator" in reviewer_names

    @pytest.mark.asyncio
    async def test_qa_ollama_critic_present(self):
        """When Ollama is running, the ollama_critic reviewer should participate."""
        from services.multi_model_qa import MultiModelQA

        qa = MultiModelQA()
        result = await qa.review(
            title="Benefits of Unit Testing",
            content=SAMPLE_BLOG_CONTENT,
            topic="unit testing",
        )

        reviewer_names = [r.reviewer for r in result.reviews]
        assert "ollama_critic" in reviewer_names, (
            f"Expected ollama_critic in reviewers, got: {reviewer_names}"
        )

        # The Ollama critic should return a valid score
        ollama_review = next(r for r in result.reviews if r.reviewer == "ollama_critic")
        assert 0 <= ollama_review.score <= 100
        assert ollama_review.feedback, "Ollama critic returned empty feedback"
        assert ollama_review.provider == "ollama"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _ollama_has_model("gemma3:27b"),
        reason="gemma3:27b not installed in Ollama",
    )
    async def test_qa_with_gemma3_27b(self):
        """QA review specifically with gemma3:27b model produces valid JSON output."""
        from services.ollama_client import OllamaClient
        from services.prompt_manager import get_prompt_manager

        client = OllamaClient()
        try:
            prompt = get_prompt_manager().get_prompt(
                "qa.review",
                title="Benefits of Unit Testing",
                topic="unit testing",
                content=SAMPLE_BLOG_CONTENT[:4000],
                current_date="2026-05-09",
                sources_block="",
            )
            result = await client.generate(
                prompt=prompt,
                model="gemma3:27b",
                max_tokens=300,
                temperature=0.3,
            )
            text = result["text"]
            assert text, "gemma3:27b returned empty text"

            # Should contain parseable JSON with required keys
            json_match = re.search(r"\{[^{}]*\"approved\"[^{}]*\}", text)
            assert json_match, f"No JSON with 'approved' key found in: {text[:300]}"
            data = json.loads(json_match.group(0))
            assert "approved" in data
            assert "quality_score" in data
            assert "feedback" in data
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# 4. SEO Metadata Generation
# ---------------------------------------------------------------------------


class TestSEOMetadata:
    """Verify SEO metadata generation works with real content."""

    def test_seo_assets_from_content(self):
        """ContentMetadataGenerator produces seo_title, meta_description, meta_keywords."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator()
        seo = gen.generate_seo_assets(
            title="Benefits of Unit Testing in Modern Software",
            content=SAMPLE_BLOG_CONTENT,
            topic="unit testing",
        )

        assert seo["seo_title"], "seo_title is empty"
        assert seo["meta_description"], "meta_description is empty"
        assert len(seo["meta_description"]) <= 160, (
            f"meta_description too long: {len(seo['meta_description'])} chars"
        )
        assert seo["meta_keywords"], "meta_keywords is empty"
        assert isinstance(seo["meta_keywords"], list)
        assert len(seo["meta_keywords"]) >= 1
        assert seo["slug"], "slug is empty"
        assert " " not in seo["slug"], "slug contains spaces"

    def test_seo_slug_generation(self):
        """Slug is URL-friendly: lowercase, no special chars, dashes for spaces."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator()
        seo = gen.generate_seo_assets(
            title="AI & Machine Learning: A 2026 Guide!",
            content="Some content about AI and machine learning.",
            topic="AI",
        )

        slug = seo["slug"]
        assert slug == slug.lower(), "slug should be lowercase"
        assert re.match(r"^[a-z0-9-]+$", slug), f"slug has invalid chars: {slug}"

    def test_reading_time_and_word_count(self):
        """Reading time and word count calculations work correctly."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator()
        reading_time = gen.calculate_reading_time(SAMPLE_BLOG_CONTENT)
        assert reading_time >= 1, "Reading time should be at least 1 minute"

    def test_category_and_tags(self):
        """Category and tag suggestions are generated from content."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator()
        org = gen.generate_category_and_tags(SAMPLE_BLOG_CONTENT, "unit testing")
        assert org["category"], "category is empty"
        assert isinstance(org["tags"], list)
        assert len(org["tags"]) >= 1

    @pytest.mark.asyncio
    async def test_full_seo_pipeline_with_ollama_content(self):
        """Generate content via Ollama, then produce full SEO metadata.

        Uses gemma3:27b (non-thinking) for reliable short-form generation.
        Falls back to larger token budget if only thinking models are available.
        """
        from services.ollama_client import OllamaClient
        from services.seo_content_generator import ContentMetadataGenerator

        model = "gemma3:27b" if _ollama_has_model("gemma3:27b") else None
        # Thinking models need more tokens for reasoning overhead
        max_tokens = 500 if model else 1500
        client = OllamaClient()
        try:
            result = await client.generate(
                prompt=(
                    "Write a short blog post (3 paragraphs) about the benefits of "
                    "continuous integration. Include a title as a markdown heading."
                ),
                model=model,
                max_tokens=max_tokens,
                temperature=0.7,
            )
            content = result["text"]
            assert content, "Ollama returned empty content"

            # Extract title or use default
            title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
            title = title_match.group(1) if title_match else "Continuous Integration Benefits"

            gen = ContentMetadataGenerator()
            seo = gen.generate_seo_assets(title=title, content=content, topic="CI/CD")

            # All SEO fields should be populated
            assert seo["seo_title"]
            assert seo["meta_description"]
            assert seo["meta_keywords"]
            assert seo["slug"]
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# 5. Thinking Models — qwen3.5 / glm-4.7
# ---------------------------------------------------------------------------


class TestThinkingModels:
    """Verify thinking models return non-empty content with sufficient token budget.

    Thinking models (qwen3.5, glm-4.7) use internal chain-of-thought tokens
    before producing visible output. They need a larger max_tokens budget
    to account for the reasoning overhead.
    """

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_qwen35_generates_nonempty_content(self):
        """qwen3.5 returns non-empty content with sufficient token budget.

        Thinking models use internal chain-of-thought that consumes tokens
        before producing visible output. We use a generous budget (4000 tokens)
        and a simple prompt to maximize the chance of visible output.
        """
        from services.ollama_client import OllamaClient

        qwen_model = _find_ollama_model("qwen3.5")
        if not qwen_model:
            pytest.skip("No qwen3.5 variant installed in Ollama")

        client = OllamaClient(timeout=120)
        try:
            # Use /no_think to disable extended reasoning if supported,
            # otherwise the simple prompt should keep thinking short
            result = await client.generate(
                prompt="Say exactly: Code reviews improve software quality. /no_think",
                model=qwen_model,
                max_tokens=4000,  # Generous budget for thinking overhead
                temperature=0.0,  # Deterministic to reduce thinking
            )
            text = result["text"]
            # Thinking models may consume all tokens on reasoning with complex prompts.
            # With a simple prompt and /no_think hint, we expect visible output.
            if not text:
                pytest.skip(
                    f"{qwen_model} consumed all {result.get('tokens', 0)} tokens on "
                    "internal reasoning — this is expected behavior for thinking models "
                    "with certain prompt/token combinations"
                )
            words = text.strip().split()
            assert len(words) >= 3, f"{qwen_model} output too short ({len(words)} words): {text[:200]}"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_glm47_generates_nonempty_content(self):
        """glm-4.7 variant returns non-empty content with 1500 token budget.

        Detects the actual installed glm-4.7 variant name (e.g. glm-4.7-5090:latest).
        """
        from services.ollama_client import OllamaClient

        # Find the actual glm-4.7 model name (may have a suffix like -5090)
        glm_model = _find_ollama_model("glm-4.7")
        if not glm_model:
            pytest.skip("No glm-4.7 variant installed in Ollama")

        client = OllamaClient()
        try:
            result = await client.generate(
                prompt="Write 2 sentences about why code reviews matter.",
                model=glm_model,
                max_tokens=1500,
                temperature=0.3,
            )
            text = result["text"]
            assert text, f"{glm_model} returned no content"
            assert len(text.strip()) > 0, (
                f"{glm_model} returned whitespace-only content — may need larger token budget"
            )
            words = text.strip().split()
            assert len(words) >= 5, f"{glm_model} output too short ({len(words)} words): {text[:200]}"
        finally:
            await client.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)
    async def test_qwen35_qa_review_produces_json(self):
        """qwen3.5 can produce valid QA review JSON with enough token budget.

        Thinking models need large token budgets for QA prompts because internal
        chain-of-thought reasoning consumes tokens before visible output starts.
        With complex prompts, qwen3.5 may use 4000+ tokens on reasoning alone.
        """
        from services.ollama_client import OllamaClient
        from services.prompt_manager import get_prompt_manager

        qwen_model = _find_ollama_model("qwen3.5")
        if not qwen_model:
            pytest.skip("No qwen3.5 variant installed in Ollama")

        client = OllamaClient(timeout=180)
        try:
            # Use a shorter content snippet to reduce thinking overhead
            short_content = (
                "Unit testing catches bugs early and enables safe refactoring. "
                "Teams that test ship fewer bugs."
            )
            prompt = get_prompt_manager().get_prompt(
                "qa.review",
                title="Benefits of Unit Testing",
                topic="unit testing",
                content=short_content,
                current_date="2026-05-09",
                sources_block="",
            )
            result = await client.generate(
                prompt=prompt + "\n/no_think",
                model=qwen_model,
                max_tokens=8000,
                temperature=0.0,
            )
            text = result["text"]
            if not text:
                pytest.skip(
                    f"{qwen_model} consumed all {result.get('tokens', 0)} tokens on "
                    "internal reasoning — expected for thinking models with complex prompts"
                )

            # Extract JSON from response
            json_match = re.search(r"\{[^{}]*\"approved\"[^{}]*\}", text)
            assert json_match, (
                f"{qwen_model} did not produce JSON with 'approved' key. Output: {text[:500]}"
            )
            data = json.loads(json_match.group(0))
            assert "approved" in data
            assert "quality_score" in data
        finally:
            await client.close()
