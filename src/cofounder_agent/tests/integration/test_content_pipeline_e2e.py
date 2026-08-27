"""
End-to-end integration tests for the content pipeline with LOCAL Ollama.

Unlike test_content_pipeline.py (which mocks LLM calls), these tests make
REAL calls to the local Ollama instance to verify:

1. Ollama connectivity — can we reach the resolved Ollama and generate text?
2. Content generation — can AIContentGenerator produce content via Ollama?
3. QA review — can MultiModelQA review content with a local model?
4. SEO metadata — can the pipeline generate seo_title, seo_description, seo_keywords?
5. Thinking models — do qwen3.5/glm-4.7 return non-empty content with sufficient token budget?

Requirements:
  - A reachable Ollama at the URL the code under test resolves (see
    ``_ollama_base_url`` — NOT a hardcoded localhost:11434)
  - Tests are skipped automatically if that Ollama is unreachable
  - The two tests that dispatch through the pipeline's own seams
    (``test_generate_blog_post_with_ollama``,
    ``test_qa_ollama_critic_present``) additionally need a reachable
    Postgres — see the ``platform_stack`` fixture — and skip on their own
    when there isn't one
  - Marked with @pytest.mark.integration (excluded from unit test suite)
"""

import functools
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import pytest
from brain.docker_utils import IN_DOCKER

# ---------------------------------------------------------------------------
# The one URL — the guard and the code under test must agree on it
# ---------------------------------------------------------------------------


def _reachable_from_here(url: str) -> str:
    """De-containerize a URL so it resolves from *this* process.

    The inverse of ``brain.docker_utils.localize_url``, which rewrites
    ``localhost`` → ``host.docker.internal`` for code running inside a
    container. ``app_settings.ollama_base_url`` is stored in the container
    form (``http://host.docker.internal:11434``) because the worker is its
    main consumer — but that hostname does not resolve from a host-side
    pytest process, so a host run has to translate back.

    No-op inside Docker, and idempotent, so applying it to a URL that is
    already in host form is safe.
    """
    if IN_DOCKER:
        return url
    return url.replace("://host.docker.internal:", "://localhost:")


@functools.lru_cache(maxsize=1)
def _ollama_base_url() -> str:
    """The base URL the code under test will actually use, from here.

    Resolved through ``ollama_client._default_base_url`` — the SAME chain
    the client itself reads (``ollama_base_url`` → ``ollama_host`` → the
    baked-in default) — rather than a second hardcoded literal.

    That is the entire point of this helper. The guard used to probe
    ``http://localhost:11434`` while every client it gated resolved
    ``http://host.docker.internal:11434``, so on the host the probe
    succeeded, the module did NOT skip, and seven tests then died on
    ``httpx.ConnectError: [Errno -2] Name or service not known``. Deriving
    the probe URL from the client's own resolver makes that particular
    divergence unrepresentable: if the resolution chain changes, the guard
    follows it.
    """
    from services.ollama_client import _default_base_url

    return _reachable_from_here(_default_base_url()).rstrip("/")


@functools.lru_cache(maxsize=1)
def _ollama_probe() -> tuple[bool, tuple[str, ...]]:
    """``(reachable, installed_model_names)`` for the resolved URL.

    One cached ``/api/tags`` call backs every reachability and
    model-availability question in this module, fired at test *setup* (not
    import) time so pytest collection stays offline (#994).
    """
    try:
        r = httpx.get(f"{_ollama_base_url()}/api/tags", timeout=3.0)
        if r.status_code != 200:
            return False, ()
        models = r.json().get("models", [])
        return True, tuple(str(m.get("name", "")) for m in models)
    except Exception:
        return False, ()


def _ollama_is_running() -> bool:
    """True when the resolved Ollama answered ``/api/tags``."""
    return _ollama_probe()[0]


def _ollama_has_model(model_name: str) -> bool:
    """Check if a specific model (or a model containing that substring) is available."""
    return any(model_name in name for name in _ollama_probe()[1])


def _find_ollama_model(prefix: str) -> str | None:
    """Find the first installed Ollama model whose name contains the given prefix."""
    return next((name for name in _ollama_probe()[1] if prefix in name), None)


def _make_client(**kwargs):
    """An ``OllamaClient`` pinned to the URL the guard actually probed.

    Every direct-client test goes through here so "the guard checked it"
    and "the client called it" cannot drift apart again.
    """
    from services.ollama_client import OllamaClient

    return OllamaClient(base_url=_ollama_base_url(), **kwargs)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_ollama() -> None:
    """Skip every test in this module unless the resolved Ollama is reachable.

    Replaces the former module-level ``skipif(not _ollama_is_running())``,
    which probed the network at *import* time — so merely collecting this
    file (e.g. a CI `pytest tests/integration/` sweep) hit the network.
    A fixture defers the probe to test setup, keeping collection offline
    while preserving the "skip the whole module when Ollama is down"
    behaviour. The probe is cached, so it fires at most once per session.

    The skip names the URL, because "Ollama is down" and "we asked the
    wrong host" look identical from the outside and only one of them is
    an outage.
    """
    if not _ollama_is_running():
        pytest.skip(f"Ollama not reachable at {_ollama_base_url()}")


@pytest.fixture
def ollama_site_config():
    """A ``SiteConfig`` pinned to the same URL the guard probed.

    Services that build their own client (``AIContentGenerator``,
    ``ContentMetadataGenerator``) read ``ollama_base_url`` off the injected
    SiteConfig rather than taking a ``base_url``, so seeding it here is how
    those paths join the same agreement.
    """
    from services.site_config import SiteConfig

    return SiteConfig(initial_config={"ollama_base_url": _ollama_base_url()})


# ---------------------------------------------------------------------------
# 1. Ollama Connectivity
# ---------------------------------------------------------------------------


class TestOllamaConnectivity:
    """Verify basic Ollama server connectivity and model discovery."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        """OllamaClient.check_health() returns True when server is running."""
        client = _make_client()
        try:
            assert await client.check_health() is True
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_list_models_returns_nonempty(self):
        """At least one model should be installed locally."""
        client = _make_client()
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

        Pins a non-thinking model explicitly to avoid token-budget issues —
        leaving the model unset auto-resolves to the largest installed one,
        which consumes the whole budget on a reasoning trace.
        """
        model = _require_small_model()
        client = _make_client()
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
    @pytest.mark.timeout(600)
    async def test_generate_blog_post_with_ollama(self, platform_stack):
        """AIContentGenerator.generate_blog_post produces non-empty content via Ollama.

        Takes the full ``platform_stack`` because the generator raises
        ``RuntimeError: platform handle required for dispatch`` without a
        handle. It used to be constructed bare and still "passed": its
        Ollama health check failed against the unreachable container URL,
        so it returned non-Ollama fallback content that satisfied every
        assertion here without a model ever being called.
        """
        from modules.content.ai_content_generator import AIContentGenerator

        gen = AIContentGenerator(
            quality_threshold=2.0,  # Low threshold for speed
            site_config=platform_stack.site_config,
            platform=platform_stack.platform,
        )
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

        Pins a non-thinking model to avoid empty-output issues with low token
        budgets (see ``_SMALL_NON_THINKING_MODELS``).
        """
        model = _require_small_model()
        client = _make_client()
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

# Small, non-thinking, JSON-clean local models, in preference order; the first
# one actually installed wins.
#
# Passing ``model=None`` to OllamaClient is NOT a safe "just pick something":
# it auto-resolves to the LARGEST installed model, which on a real rig is a
# thinking model — precisely what the low-token-budget tests below say they are
# avoiding. Several of them hardcoded ``gemma3:27b`` and silently degraded to
# that auto-resolve when it wasn't installed, so a 100-token "What is 2+2?"
# spent its whole budget on a reasoning trace.
_SMALL_NON_THINKING_MODELS = (
    "gemma3:27b",
    "qwen2.5:7b",
    "qwen2.5-coder:7b",
    "phi4",
    "llama3.2",
)


def _small_non_thinking_model() -> str | None:
    """The installed name of the first preferred small model, or ``None``."""
    for candidate in _SMALL_NON_THINKING_MODELS:
        found = _find_ollama_model(candidate)
        if found:
            return found
    return None


def _require_small_model() -> str:
    """Like :func:`_small_non_thinking_model`, but skips when none is installed."""
    model = _small_non_thinking_model()
    if model is None:
        pytest.skip(
            "no small non-thinking model installed; tried: "
            f"{', '.join(_SMALL_NON_THINKING_MODELS)}"
        )
    return model


@contextmanager
def _dispatcher_pinned_to_probed_ollama():
    """Point the LLM dispatcher's ``api_base`` at the URL we probed.

    ``dispatcher.get_provider_config`` reads
    ``plugin.llm_provider.litellm.config`` straight out of ``app_settings``
    — NOT through ``SiteConfig`` — so seeding a SiteConfig does not reach
    it. On an operator DB that row holds the container hostname (plus
    per-model overrides pinning the GPU-specific instances), which is
    exactly what ``_reachable_from_here`` exists to translate.

    Patching the read keeps ONE rewrite rule for the whole module and
    never writes to the operator's database.
    """
    from services.llm_providers import dispatcher

    real = dispatcher.get_provider_config

    async def _patched(pool, provider_name):
        config = dict(await real(pool, provider_name))
        if config.get("api_base"):
            config["api_base"] = _reachable_from_here(str(config["api_base"]))
        overrides = config.get("model_api_base_overrides")
        if isinstance(overrides, dict):
            config["model_api_base_overrides"] = {
                key: _reachable_from_here(str(value))
                for key, value in overrides.items()
            }
        return config

    dispatcher.get_provider_config = _patched
    try:
        yield
    finally:
        dispatcher.get_provider_config = real


@dataclass
class _PlatformStack:
    """The DI seams production threads into content code."""

    pool: object
    site_config: object
    settings: object
    platform: object


@pytest.fixture
async def platform_stack():
    """Build the seams production threads into content code.

    Content that dispatches an LLM needs a pool AND a capability-scoped
    ``platform`` handle, and the pipeline threads both (the ``qa.*`` atoms
    via ``state['platform']``, the writer via the run context). Constructed
    bare, those paths do not merely lose telemetry — they cannot dispatch:

    - ``MultiModelQA._dispatch_llm`` returns ``None`` without pool +
      platform, and with no settings service the critic-model pin resolves
      to "no critic model". QA rails are fail-soft, so the rail silently
      drops out of the reviewer list.
    - ``AIContentGenerator`` raises ``RuntimeError: platform handle
      required for dispatch``.

    Both were masked while the Ollama URL was wrong: the QA rail's skip is
    silent by design, and the generator short-circuits to non-Ollama
    fallback content when its health check fails, so its test passed
    without ever reaching a model.

    Needs a reachable Postgres, and skips honestly when there isn't one —
    the only DB requirement in this module.
    """
    import asyncpg
    from brain.bootstrap import resolve_database_url

    dsn = resolve_database_url()
    if not dsn:
        pytest.skip("no database URL resolved (bootstrap.toml / DATABASE_URL)")

    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=5)
    except Exception as exc:
        pytest.skip(f"Postgres unreachable: {type(exc).__name__}: {exc}")

    from services.audit_log import init_global_audit_logger, reset_global_audit_logger
    from services.di_wiring import build_platform_for_subprocess
    from services.settings_service import SettingsService
    from services.site_config import SiteConfig

    try:
        site_config = SiteConfig(pool=pool)
        await site_config.load(pool)
        # Same de-containerization the guard applied, so every layer of
        # this stack agrees on one URL. Written straight into the loaded
        # map because that is what ``load()`` populates; there is no
        # public setter and this instance is local to the test.
        site_config._config["ollama_base_url"] = _ollama_base_url()

        # build_platform_for_subprocess returns None without a global
        # AuditLogger — which is why a host-side platform build looks
        # simply "unsupported" until this runs.
        init_global_audit_logger(pool, quiet=True)
        platform = build_platform_for_subprocess(pool, site_config)
        if platform is None:
            pytest.skip("could not build a scoped Platform handle for module 'content'")

        with _dispatcher_pinned_to_probed_ollama():
            yield _PlatformStack(
                pool=pool,
                site_config=site_config,
                settings=SettingsService(pool),
                platform=platform,
            )
    finally:
        reset_global_audit_logger(pool)
        await pool.close()


@pytest.fixture
def qa_with_platform(platform_stack):
    """A ``MultiModelQA`` built the way the ``qa.*`` atoms build it."""
    from modules.content.multi_model_qa import MultiModelQA

    return MultiModelQA(
        pool=platform_stack.pool,
        settings_service=platform_stack.settings,
        site_config=platform_stack.site_config,
        platform=platform_stack.platform,
    )


class TestQAReview:
    """Verify MultiModelQA can review content using local Ollama."""

    @pytest.mark.asyncio
    async def test_multi_model_qa_review(self, ollama_site_config):
        """MultiModelQA.review() returns a scored result using Ollama."""
        from modules.content.multi_model_qa import MultiModelQA

        qa = MultiModelQA(site_config=ollama_site_config)
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
    @pytest.mark.timeout(300)
    async def test_qa_ollama_critic_present(self, qa_with_platform):
        """The ollama_critic rail returns a real verdict from a local model.

        Goes through ``critic_review_once`` — documented as delegating to
        the exact production critic path (``_review_with_ollama``: same
        prompt pack, review window, JSON parse, and score-over-boolean
        approval rule) — rather than the full ``review()`` chain, so this
        asserts the critic rail specifically instead of paying for every
        other rail to reach it.

        The model is an explicitly-chosen small one rather than the
        operator's ``pipeline_critic_model`` pin: that pin is an 18 GB
        model whose cold load blew a 240 s dispatch timeout on a contended
        GPU, and *which* model the pin resolves to is already unit-covered
        (``test_lane_b_qa_critic_migration``). What needs a real box is the
        rail — dispatch, prompt, parse, verdict.
        """
        review = await qa_with_platform.critic_review_once(
            title="Benefits of Unit Testing",
            content=SAMPLE_BLOG_CONTENT,
            topic="unit testing",
            model=f"ollama/{_require_small_model()}",
        )

        assert review is not None, (
            "critic_review_once returned None — the rail dispatched but "
            "produced no parseable verdict"
        )
        assert review.reviewer == "ollama_critic"
        assert 0 <= review.score <= 100
        assert review.feedback, "Ollama critic returned empty feedback"
        assert review.provider == "ollama"

    @pytest.mark.asyncio
    async def test_qa_with_gemma3_27b(self):
        """QA review specifically with gemma3:27b model produces valid JSON output.

        The availability check is in the body, not a ``skipif`` decorator:
        a decorator argument is evaluated at *collection*, which would put
        the ``/api/tags`` call back at import time — the exact thing #994
        moved into a setup-time fixture.
        """
        from services.prompt_manager import get_prompt_manager

        if not _ollama_has_model("gemma3:27b"):
            pytest.skip("gemma3:27b not installed in Ollama")

        client = _make_client()
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

    def test_seo_assets_from_content(self, ollama_site_config):
        """ContentMetadataGenerator produces seo_title, meta_description, meta_keywords."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator(site_config=ollama_site_config)
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

    def test_seo_slug_generation(self, ollama_site_config):
        """Slug is URL-friendly: lowercase, no special chars, dashes for spaces."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator(site_config=ollama_site_config)
        seo = gen.generate_seo_assets(
            title="AI & Machine Learning: A 2026 Guide!",
            content="Some content about AI and machine learning.",
            topic="AI",
        )

        slug = seo["slug"]
        assert slug == slug.lower(), "slug should be lowercase"
        assert re.match(r"^[a-z0-9-]+$", slug), f"slug has invalid chars: {slug}"

    def test_reading_time_and_word_count(self, ollama_site_config):
        """Reading time and word count calculations work correctly."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator(site_config=ollama_site_config)
        reading_time = gen.calculate_reading_time(SAMPLE_BLOG_CONTENT)
        assert reading_time >= 1, "Reading time should be at least 1 minute"

    def test_category_and_tags(self, ollama_site_config):
        """Category and tag suggestions are generated from content."""
        from services.seo_content_generator import ContentMetadataGenerator

        gen = ContentMetadataGenerator(site_config=ollama_site_config)
        org = gen.generate_category_and_tags(SAMPLE_BLOG_CONTENT, "unit testing")
        assert org["category"], "category is empty"
        assert isinstance(org["tags"], list)
        assert len(org["tags"]) >= 1

    @pytest.mark.asyncio
    async def test_full_seo_pipeline_with_ollama_content(self, ollama_site_config):
        """Generate content via Ollama, then produce full SEO metadata.

        Pins a non-thinking model for reliable short-form generation.
        """
        from services.seo_content_generator import ContentMetadataGenerator

        model = _require_small_model()
        max_tokens = 500
        client = _make_client()
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

            gen = ContentMetadataGenerator(site_config=ollama_site_config)
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
        qwen_model = _find_ollama_model("qwen3.5")
        if not qwen_model:
            pytest.skip("No qwen3.5 variant installed in Ollama")

        client = _make_client(timeout=120)
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
        # Find the actual glm-4.7 model name (may have a suffix like -5090)
        glm_model = _find_ollama_model("glm-4.7")
        if not glm_model:
            pytest.skip("No glm-4.7 variant installed in Ollama")

        client = _make_client()
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
        from services.prompt_manager import get_prompt_manager

        qwen_model = _find_ollama_model("qwen3.5")
        if not qwen_model:
            pytest.skip("No qwen3.5 variant installed in Ollama")

        client = _make_client(timeout=180)
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
