"""Unit tests for social_poster.py — social media copy generation.

``social_poster`` is a pure copy generator now; distribution is owned by the
``social.generate_drafts`` atom (Postiz). The legacy direct-adapter distribution
tests (generate_and_distribute / _notify / _distribute_to_adapters / adapter
counters) were removed 2026-06-29 with that path.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from services.site_config import SiteConfig
from services.social_poster import (
    SocialPost,
    _build_linkedin_prompt,
    _build_twitter_prompt,
    _generate_social_text,
    _linkedin_char_limit,
    _twitter_char_limit,
    generate_social_posts,
)

# SiteConfig DI (#272 Phase-2e): the module-level ``site_config`` global +
# ``set_site_config`` were removed; the public entries and every internal
# helper take a required ``site_config=`` kwarg. Tests thread this shared
# env-backed instance.
_TEST_SC = SiteConfig()

# Module-level constants were removed 2026-05-01 — they captured at import
# time and bypassed app_settings hot-reload. Tests now call the helper
# functions which read at call time. These local aliases keep the rest of
# the test suite tidy without regressing the runtime fix.
TWITTER_CHAR_LIMIT = _twitter_char_limit(site_config=_TEST_SC)
LINKEDIN_CHAR_LIMIT = _linkedin_char_limit(site_config=_TEST_SC)


@pytest.fixture(autouse=True)
def _autopatch_resolve_social_model():
    """Lane B sweep — short-circuit ``_resolve_social_model`` in this suite.

    The production resolver hits the cost-tier dispatcher and the
    ``social_poster_fallback_model`` setting; in unit tests neither
    exists, so the resolver would raise + notify_operator and every
    LLM-mocked test would receive an empty string. Patching the helper
    here keeps the LLM-mocked tests focused on prompt/post behavior.
    The dedicated ``test_lane_b_misc_migration.py`` suite pins the
    resolver branches.
    """
    with patch(
        "services.social_poster._resolve_social_model",
        AsyncMock(return_value="ollama/gemma3:27b"),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ollama_mock(text: str = "Great post about AI!") -> AsyncMock:
    """Return an OllamaClient mock whose generate() resolves to *text*."""
    mock = AsyncMock()
    mock.generate.return_value = {"text": text}
    return mock


SAMPLE_TITLE = "Why Local LLMs Beat Cloud APIs"
SAMPLE_SLUG = "why-local-llms-beat-cloud-apis"
SAMPLE_EXCERPT = "A deep dive into cost, latency, and privacy."
SAMPLE_KEYWORDS = ["LLM", "Ollama", "self-hosting"]


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestBuildTwitterPrompt:
    """Verify the Twitter prompt contains required elements."""

    def test_contains_title_and_excerpt(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert SAMPLE_TITLE in prompt
        assert SAMPLE_EXCERPT in prompt

    def test_contains_post_url(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert f"/posts/{SAMPLE_SLUG}" in prompt

    def test_contains_hashtags(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert "#LLM" in prompt
        assert "#Ollama" in prompt
        assert "#self-hosting" in prompt  # hyphen preserved, spaces removed

    def test_limits_to_three_hashtags(self):
        many_keywords = ["AI", "ML", "LLM", "GPU", "Cloud"]
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, many_keywords, site_config=_TEST_SC)
        # Only first 3 should appear in suggested hashtags line
        assert "#AI" in prompt
        assert "#ML" in prompt
        assert "#LLM" in prompt
        assert "#GPU" not in prompt

    def test_mentions_char_limit(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert str(TWITTER_CHAR_LIMIT) in prompt

    def test_empty_keywords(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, [], site_config=_TEST_SC)
        assert "Suggested hashtags:" in prompt


class TestBuildLinkedInPrompt:
    """Verify the LinkedIn prompt contains required elements."""

    def test_contains_title_and_excerpt(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert SAMPLE_TITLE in prompt
        assert SAMPLE_EXCERPT in prompt

    def test_contains_post_url(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert f"/posts/{SAMPLE_SLUG}" in prompt

    def test_mentions_char_limit(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert str(LINKEDIN_CHAR_LIMIT) in prompt

    def test_mentions_professional_tone(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, site_config=_TEST_SC)
        assert "professional" in prompt.lower()


# ---------------------------------------------------------------------------
# LLM text generation
# ---------------------------------------------------------------------------


class TestGenerateSocialText:
    """Test _generate_social_text with mocked OllamaClient."""

    @pytest.mark.asyncio
    async def test_returns_generated_text(self):
        ollama = _make_ollama_mock("Check out our latest blog post! #AI")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == "Check out our latest blog post! #AI"

    @pytest.mark.asyncio
    async def test_strips_wrapping_quotes(self):
        ollama = _make_ollama_mock('"Here is a tweet about AI"')
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert not result.startswith('"')
        assert not result.endswith('"')
        assert result == "Here is a tweet about AI"

    @pytest.mark.asyncio
    async def test_truncates_over_limit(self):
        long_text = "word " * 100  # Well over 280 chars
        ollama = _make_ollama_mock(long_text)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert len(result) <= TWITTER_CHAR_LIMIT
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_truncates_linkedin_over_limit(self):
        long_text = "word " * 200  # Well over 700 chars
        ollama = _make_ollama_mock(long_text)
        result = await _generate_social_text("prompt", LINKEDIN_CHAR_LIMIT, "linkedin", ollama, site_config=_TEST_SC)
        assert len(result) <= LINKEDIN_CHAR_LIMIT
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_disables_thinking_for_social_copy(self):
        """Social copy is short — a reasoning model would burn its whole
        token budget thinking and never emit the post, then OllamaClient
        salvages the thinking trace (analysis that reads like QA results)
        as the 'draft'. Generation must request think=False so the model
        emits the post directly."""
        ollama = _make_ollama_mock("Punchy tweet! #AI")
        await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert ollama.generate.await_count == 1
        assert ollama.generate.call_args.kwargs.get("think") is False

    @pytest.mark.asyncio
    async def test_strips_residual_think_block(self):
        """Defense in depth: if a model still emits an inline
        <think>...</think> reasoning block despite think=False, it must be
        stripped so the social draft never shows the model's analysis."""
        ollama = _make_ollama_mock(
            "<think>1. Analyze the request. 2. Draft ideas...</think>Clean tweet about AI #ML"
        )
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == "Clean tweet about AI #ML"
        assert "<think>" not in result and "Analyze the request" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_error(self):
        ollama = AsyncMock()
        ollama.generate.side_effect = Exception("Ollama is down")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_when_text_key_missing(self):
        ollama = AsyncMock()
        ollama.generate.return_value = {}
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == ""

    @pytest.mark.asyncio
    async def test_text_under_limit_not_truncated(self):
        short = "Short tweet #AI"
        ollama = _make_ollama_mock(short)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == short
        assert "..." not in result

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        ollama = _make_ollama_mock("  padded text  ")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == "padded text"


# ---------------------------------------------------------------------------
# generate_social_posts (public API)
# ---------------------------------------------------------------------------


class TestGenerateSocialPosts:
    """Test the main generate_social_posts function."""

    @pytest.mark.asyncio
    async def test_returns_four_posts(self):
        ollama = _make_ollama_mock("Great AI post! #LLM")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        # twitter + linkedin generated; bluesky + mastodon reuse the tweet copy
        assert len(posts) == 4
        platforms = {p.platform for p in posts}
        assert platforms == {"twitter", "linkedin", "bluesky", "mastodon"}

    @pytest.mark.asyncio
    async def test_bluesky_and_mastodon_reuse_tweet_copy(self):
        ollama = _make_ollama_mock("Great AI post! #LLM")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        by_platform = {p.platform: p.text for p in posts}
        assert by_platform["bluesky"] == by_platform["twitter"]
        assert by_platform["mastodon"] == by_platform["twitter"]

    @pytest.mark.asyncio
    async def test_posts_have_correct_url(self):
        ollama = _make_ollama_mock("Check it out!")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        for post in posts:
            assert SAMPLE_SLUG in post.post_url
            assert post.post_url.endswith(f"/posts/{SAMPLE_SLUG}")

    @pytest.mark.asyncio
    async def test_posts_are_social_post_instances(self):
        ollama = _make_ollama_mock("AI is great!")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        for post in posts:
            assert isinstance(post, SocialPost)
            assert isinstance(post.created_at, datetime)
            assert post.posted is False

    @pytest.mark.asyncio
    async def test_empty_result_when_llm_fails(self):
        ollama = AsyncMock()
        ollama.generate.side_effect = Exception("model not loaded")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        assert posts == []

    @pytest.mark.asyncio
    async def test_defaults_keywords_to_empty(self):
        ollama = _make_ollama_mock("No keywords here")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, ollama=ollama, site_config=_TEST_SC
        )
        assert len(posts) == 4

    @pytest.mark.asyncio
    async def test_partial_failure_still_returns_short_form_posts(self):
        """Twitter succeeds, LinkedIn fails — we still get twitter + the
        bluesky/mastodon copies that reuse the tweet text (3 total)."""
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"text": "Twitter post works!"}
            raise Exception("LinkedIn generation failed")

        ollama = AsyncMock()
        ollama.generate.side_effect = _side_effect
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama, site_config=_TEST_SC
        )
        platforms = {p.platform for p in posts}
        assert platforms == {"twitter", "bluesky", "mastodon"}
        assert "linkedin" not in platforms


# ---------------------------------------------------------------------------
# SocialPost dataclass
# ---------------------------------------------------------------------------


class TestSocialPostDataclass:
    """Verify SocialPost defaults and fields."""

    def test_default_posted_false(self):
        post = SocialPost(platform="twitter", text="Hello", post_url="https://example.com")
        assert post.posted is False

    def test_created_at_is_utc(self):
        post = SocialPost(platform="linkedin", text="Hello", post_url="https://example.com")
        assert post.created_at.tzinfo == timezone.utc

    def test_fields_stored(self):
        post = SocialPost(platform="twitter", text="My tweet", post_url="https://x.com/post")
        assert post.platform == "twitter"
        assert post.text == "My tweet"
        assert post.post_url == "https://x.com/post"


# ---------------------------------------------------------------------------
# Character limit / hashtag edge cases
# ---------------------------------------------------------------------------


class TestCharacterLimitsAndHashtags:
    """Edge cases for character limits and hashtag generation."""

    def test_hashtags_strip_spaces(self):
        keywords = ["machine learning", "deep learning", "natural language processing"]
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, keywords, site_config=_TEST_SC)
        assert "#machinelearning" in prompt
        assert "#deeplearning" in prompt
        assert "#naturallanguageprocessing" in prompt

    def test_twitter_limit_constant(self):
        assert TWITTER_CHAR_LIMIT == 280

    def test_linkedin_limit_constant(self):
        assert LINKEDIN_CHAR_LIMIT == 700

    @pytest.mark.asyncio
    async def test_exact_limit_text_not_truncated(self):
        exact_text = "x" * TWITTER_CHAR_LIMIT
        ollama = _make_ollama_mock(exact_text)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert result == exact_text
        assert len(result) == TWITTER_CHAR_LIMIT

    @pytest.mark.asyncio
    async def test_one_over_limit_gets_truncated(self):
        over_text = "x " * 141  # 282 chars, just over 280
        ollama = _make_ollama_mock(over_text)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama, site_config=_TEST_SC)
        assert len(result) <= TWITTER_CHAR_LIMIT
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# dispatch_complete path (poindexter#706)
# ---------------------------------------------------------------------------


class TestGenerateSocialTextDispatchPath:
    """When site_config._pool is wired, _generate_social_text routes through
    dispatch_complete instead of OllamaClient (poindexter#706)."""

    def _sc_with_pool(self) -> "SiteConfig":
        from unittest.mock import MagicMock

        class _FakeConn:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def fetchval(self, *a): return "ollama/gemma3:27b"

        class _FakePool:
            def acquire(self): return _FakeConn()

        sc = MagicMock(spec=SiteConfig)
        sc._pool = _FakePool()
        sc.get.side_effect = lambda k, d=None: {"social_poster_max_tokens": 300}.get(k, d)
        sc.get_int.side_effect = lambda k, d=None: {"social_poster_max_tokens": 300}.get(k, d)
        return sc

    @pytest.mark.asyncio
    async def test_calls_dispatch_complete_not_ollama(self):
        """When pool is wired and no explicit ollama arg, dispatch_complete is
        called and OllamaClient is never instantiated."""
        from unittest.mock import MagicMock

        completion = MagicMock()
        completion.text = "Great AI post! #LLM"

        with patch(
            "services.social_poster.dispatch_complete",
            new=AsyncMock(return_value=completion),
        ) as mock_dispatch, patch(
            "services.social_poster.OllamaClient",
        ) as mock_ollama_cls:
            result = await _generate_social_text(
                "test prompt", 280, "twitter", site_config=self._sc_with_pool()
            )

        assert result == "Great AI post! #LLM"
        mock_dispatch.assert_awaited_once()
        mock_ollama_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_complete_receives_correct_tier_and_phase(self):
        """dispatch_complete must be called with tier='standard' and
        phase='social_poster'."""
        from unittest.mock import MagicMock

        completion = MagicMock()
        completion.text = "Tweet text"

        with patch(
            "services.social_poster.dispatch_complete",
            new=AsyncMock(return_value=completion),
        ) as mock_dispatch:
            await _generate_social_text(
                "prompt", 280, "twitter", site_config=self._sc_with_pool()
            )

        kwargs = mock_dispatch.await_args.kwargs
        assert kwargs.get("tier") == "standard"
        assert kwargs.get("phase") == "social_poster"

    @pytest.mark.asyncio
    async def test_explicit_ollama_arg_bypasses_dispatch(self):
        """Callers that pass an explicit OllamaClient (legacy test / bootstrap
        compatibility) use the OllamaClient path even when pool is wired."""
        ollama = _make_ollama_mock("Legacy path text")
        sc_with_pool = self._sc_with_pool()

        with patch(
            "services.social_poster.dispatch_complete",
            new=AsyncMock(),
        ) as mock_dispatch:
            result = await _generate_social_text(
                "prompt", 280, "twitter", ollama, site_config=sc_with_pool
            )

        assert result == "Legacy path text"
        mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_error_returns_empty(self):
        """Errors from dispatch_complete are caught; empty string returned."""
        with patch(
            "services.social_poster.dispatch_complete",
            new=AsyncMock(side_effect=RuntimeError("provider offline")),
        ):
            result = await _generate_social_text(
                "prompt", 280, "twitter", site_config=self._sc_with_pool()
            )

        assert result == ""

    @pytest.mark.asyncio
    async def test_strips_think_block_from_dispatch_result(self):
        """Defense-in-depth think-block stripping still applies on the
        dispatch_complete path."""
        from unittest.mock import MagicMock

        completion = MagicMock()
        completion.text = "<think>analysis...</think>Clean tweet #AI"

        with patch(
            "services.social_poster.dispatch_complete",
            new=AsyncMock(return_value=completion),
        ):
            result = await _generate_social_text(
                "prompt", 280, "twitter", site_config=self._sc_with_pool()
            )

        assert result == "Clean tweet #AI"
        assert "<think>" not in result
