"""Unit tests for social_poster.py — social media post generation and distribution."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Importing the handler modules triggers their @register_handler decorators
# so registry.dispatch("publishing", ...) inside the dispatcher resolves.
# Same pattern test_tap_framework / test_outbound_handlers use.
from services.integrations.handlers import (  # noqa: F401
    publishing_bluesky,
    publishing_mastodon,
)
from services.social_poster import (
    SocialPost,
    _build_linkedin_prompt,
    _build_twitter_prompt,
    _generate_social_text,
    _linkedin_char_limit,
    _twitter_char_limit,
    generate_and_distribute_social_posts,
    generate_social_posts,
)

# Module-level constants were removed 2026-05-01 — they captured at import
# time and bypassed app_settings hot-reload. Tests now call the helper
# functions which read at call time. These local aliases keep the rest of
# the test suite tidy without regressing the runtime fix.
TWITTER_CHAR_LIMIT = _twitter_char_limit()
LINKEDIN_CHAR_LIMIT = _linkedin_char_limit()


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
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert SAMPLE_TITLE in prompt
        assert SAMPLE_EXCERPT in prompt

    def test_contains_post_url(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert f"/posts/{SAMPLE_SLUG}" in prompt

    def test_contains_hashtags(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert "#LLM" in prompt
        assert "#Ollama" in prompt
        assert "#self-hosting" in prompt  # hyphen preserved, spaces removed

    def test_limits_to_three_hashtags(self):
        many_keywords = ["AI", "ML", "LLM", "GPU", "Cloud"]
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, many_keywords)
        # Only first 3 should appear in suggested hashtags line
        assert "#AI" in prompt
        assert "#ML" in prompt
        assert "#LLM" in prompt
        assert "#GPU" not in prompt

    def test_mentions_char_limit(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert str(TWITTER_CHAR_LIMIT) in prompt

    def test_empty_keywords(self):
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, [])
        assert "Suggested hashtags:" in prompt


class TestBuildLinkedInPrompt:
    """Verify the LinkedIn prompt contains required elements."""

    def test_contains_title_and_excerpt(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert SAMPLE_TITLE in prompt
        assert SAMPLE_EXCERPT in prompt

    def test_contains_post_url(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert f"/posts/{SAMPLE_SLUG}" in prompt

    def test_mentions_char_limit(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert str(LINKEDIN_CHAR_LIMIT) in prompt

    def test_mentions_professional_tone(self):
        prompt = _build_linkedin_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS)
        assert "professional" in prompt.lower()


# ---------------------------------------------------------------------------
# LLM text generation
# ---------------------------------------------------------------------------


class TestGenerateSocialText:
    """Test _generate_social_text with mocked OllamaClient."""

    @pytest.mark.asyncio
    async def test_returns_generated_text(self):
        ollama = _make_ollama_mock("Check out our latest blog post! #AI")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == "Check out our latest blog post! #AI"

    @pytest.mark.asyncio
    async def test_strips_wrapping_quotes(self):
        ollama = _make_ollama_mock('"Here is a tweet about AI"')
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert not result.startswith('"')
        assert not result.endswith('"')
        assert result == "Here is a tweet about AI"

    @pytest.mark.asyncio
    async def test_truncates_over_limit(self):
        long_text = "word " * 100  # Well over 280 chars
        ollama = _make_ollama_mock(long_text)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert len(result) <= TWITTER_CHAR_LIMIT
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_truncates_linkedin_over_limit(self):
        long_text = "word " * 200  # Well over 700 chars
        ollama = _make_ollama_mock(long_text)
        result = await _generate_social_text("prompt", LINKEDIN_CHAR_LIMIT, "linkedin", ollama)
        assert len(result) <= LINKEDIN_CHAR_LIMIT
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_returns_empty_on_llm_error(self):
        ollama = AsyncMock()
        ollama.generate.side_effect = Exception("Ollama is down")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_when_text_key_missing(self):
        ollama = AsyncMock()
        ollama.generate.return_value = {}
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == ""

    @pytest.mark.asyncio
    async def test_text_under_limit_not_truncated(self):
        short = "Short tweet #AI"
        ollama = _make_ollama_mock(short)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == short
        assert "..." not in result

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        ollama = _make_ollama_mock("  padded text  ")
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == "padded text"


# ---------------------------------------------------------------------------
# generate_social_posts (public API)
# ---------------------------------------------------------------------------


class TestGenerateSocialPosts:
    """Test the main generate_social_posts function."""

    @pytest.mark.asyncio
    async def test_returns_two_posts(self):
        ollama = _make_ollama_mock("Great AI post! #LLM")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        assert len(posts) == 2
        platforms = {p.platform for p in posts}
        assert platforms == {"twitter", "linkedin"}

    @pytest.mark.asyncio
    async def test_posts_have_correct_url(self):
        ollama = _make_ollama_mock("Check it out!")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        for post in posts:
            assert SAMPLE_SLUG in post.post_url
            assert post.post_url.endswith(f"/posts/{SAMPLE_SLUG}")

    @pytest.mark.asyncio
    async def test_posts_are_social_post_instances(self):
        ollama = _make_ollama_mock("AI is great!")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
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
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        assert posts == []

    @pytest.mark.asyncio
    async def test_defaults_keywords_to_empty(self):
        ollama = _make_ollama_mock("No keywords here")
        posts = await generate_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, ollama=ollama
        )
        assert len(posts) == 2

    @pytest.mark.asyncio
    async def test_partial_failure_returns_one_post(self):
        """If one platform fails, we still get the other."""
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
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        assert len(posts) == 1
        assert posts[0].platform == "twitter"


# ---------------------------------------------------------------------------
# generate_and_distribute_social_posts (end-to-end with notifications)
# ---------------------------------------------------------------------------


class TestGenerateAndDistribute:
    """Test the full generate-and-distribute pipeline with mocked notifications."""

    @pytest.mark.asyncio
    @patch("services.social_poster._notify", new_callable=AsyncMock)
    async def test_sends_notification_per_post(self, mock_notify):
        ollama = _make_ollama_mock("Posted! #AI")
        posts = await generate_and_distribute_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        assert len(posts) == 2
        # One notification per post (twitter + linkedin)
        assert mock_notify.call_count == 2

    @pytest.mark.asyncio
    @patch("services.social_poster._notify", new_callable=AsyncMock)
    async def test_notification_contains_platform_header(self, mock_notify):
        ollama = _make_ollama_mock("Great content!")
        await generate_and_distribute_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        messages = [call.args[0] for call in mock_notify.call_args_list]
        assert any("Twitter/X" in m for m in messages)
        assert any("LinkedIn" in m for m in messages)

    @pytest.mark.asyncio
    @patch("services.social_poster._notify", new_callable=AsyncMock)
    async def test_notification_contains_blog_url(self, mock_notify):
        ollama = _make_ollama_mock("Read this!")
        await generate_and_distribute_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        messages = [call.args[0] for call in mock_notify.call_args_list]
        assert all(SAMPLE_SLUG in m for m in messages)

    @pytest.mark.asyncio
    @patch("services.social_poster._notify", new_callable=AsyncMock)
    async def test_sends_failure_notification_when_no_posts(self, mock_notify):
        ollama = AsyncMock()
        ollama.generate.side_effect = Exception("total failure")
        posts = await generate_and_distribute_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        assert posts == []
        mock_notify.assert_called_once()
        assert "Failed" in mock_notify.call_args.args[0]

    @pytest.mark.asyncio
    @patch("services.social_poster._notify", new_callable=AsyncMock)
    async def test_returns_social_post_objects(self, mock_notify):
        ollama = _make_ollama_mock("AI tweet")
        posts = await generate_and_distribute_social_posts(
            SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, SAMPLE_KEYWORDS, ollama
        )
        for post in posts:
            assert isinstance(post, SocialPost)


# ---------------------------------------------------------------------------
# _notify internals (HTTP calls mocked)
# ---------------------------------------------------------------------------


class TestNotify:
    """Test the _notify function with mocked httpx."""

    @pytest.mark.asyncio
    @patch("services.social_poster._get_discord_ops_channel", return_value="test-discord-channel-id")
    @patch("services.social_poster.get_telegram_chat_id", return_value="test-chat-id")
    @patch("services.social_poster.get_telegram_bot_token", new_callable=AsyncMock)
    @patch("services.social_poster._openclaw_token", new_callable=AsyncMock)
    @patch("services.social_poster.httpx.AsyncClient")
    async def test_sends_telegram_and_discord(
        self, mock_client_cls, mock_openclaw_token, mock_tg_token, mock_chat, mock_channel
    ):
        from services.social_poster import _notify

        # Token + openclaw secret are async (is_secret=true rows after #325 sweep)
        mock_tg_token.return_value = "test-bot-token"
        mock_openclaw_token.return_value = "test-openclaw-token"

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _notify("Test notification message")

        assert mock_client.post.call_count == 2
        # First call: Telegram
        telegram_call = mock_client.post.call_args_list[0]
        assert "api.telegram.org" in telegram_call.args[0]
        assert "test-bot-token" in telegram_call.args[0]
        assert telegram_call.kwargs["json"]["text"] == "Test notification message"
        assert telegram_call.kwargs["json"]["chat_id"] == "test-chat-id"
        # Second call: Discord via OpenClaw
        discord_call = mock_client.post.call_args_list[1]
        assert "/hooks/agent" in discord_call.args[0]
        assert discord_call.kwargs["headers"]["Authorization"] == "Bearer test-openclaw-token"

    @pytest.mark.asyncio
    @patch("services.social_poster.httpx.AsyncClient")
    async def test_notify_handles_http_error_gracefully(self, mock_client_cls):
        from services.social_poster import _notify

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await _notify("This should not crash")

    @pytest.mark.asyncio
    @patch("services.social_poster.httpx.AsyncClient")
    async def test_notify_handles_timeout_gracefully(self, mock_client_cls):
        from services.social_poster import _notify

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timed out")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await _notify("Timeout test")


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
        prompt = _build_twitter_prompt(SAMPLE_TITLE, SAMPLE_SLUG, SAMPLE_EXCERPT, keywords)
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
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert result == exact_text
        assert len(result) == TWITTER_CHAR_LIMIT

    @pytest.mark.asyncio
    async def test_one_over_limit_gets_truncated(self):
        over_text = "x " * 141  # 282 chars, just over 280
        ollama = _make_ollama_mock(over_text)
        result = await _generate_social_text("prompt", TWITTER_CHAR_LIMIT, "twitter", ollama)
        assert len(result) <= TWITTER_CHAR_LIMIT
        assert result.endswith("...")


# ---------------------------------------------------------------------------
# Adapter distribution — graceful degradation (GH-36)
# ---------------------------------------------------------------------------


class TestSafeCallAdapter:
    """Verify ``_safe_call_adapter`` never crashes the distribution loop."""

    @pytest.mark.asyncio
    async def test_success_dict_passes_through(self):
        from services.social_poster import _safe_call_adapter

        async def _ok():
            return {"success": True, "post_id": "p1", "error": None}

        result = await _safe_call_adapter("bluesky", _ok)
        assert result["success"] is True
        assert result["post_id"] == "p1"

    @pytest.mark.asyncio
    async def test_failure_dict_passes_through(self):
        """Graceful 'not configured' returns: adapter returns {success: False}."""
        from services.social_poster import _safe_call_adapter

        async def _soft_fail():
            return {"success": False, "post_id": None, "error": "not configured"}

        result = await _safe_call_adapter("bluesky", _soft_fail)
        assert result["success"] is False
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_becomes_error_dict(self):
        from services.social_poster import _safe_call_adapter

        async def _boom():
            raise RuntimeError("something blew up")

        result = await _safe_call_adapter("bluesky", _boom)
        assert result["success"] is False
        assert result["post_id"] is None
        assert "something blew up" in result["error"]

    @pytest.mark.asyncio
    async def test_notimplementederror_becomes_skipped(self):
        """Defensive: a future stub adapter or one flipped on by mistake
        raising NotImplementedError must be classified as 'unavailable'
        (skipped outcome), not 'error' — so the error counter doesn't
        spike for known-off platforms."""
        from services.social_poster import _safe_call_adapter

        async def _stub():
            raise NotImplementedError("future-platform requires OAuth setup")

        result = await _safe_call_adapter("future-platform", _stub)
        assert result["success"] is False
        assert "unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_non_dict_result_treated_as_failure(self):
        from services.social_poster import _safe_call_adapter

        async def _bad_return():
            return "unexpected string"  # not a dict — adapter contract violated

        result = await _safe_call_adapter("bluesky", _bad_return)
        assert result["success"] is False
        assert "non-dict" in result["error"]


def _row(platform: str, *, name: str | None = None, handler_name: str | None = None):
    """Build a PublishingAdapterRow for tests with sensible defaults."""
    from uuid import uuid4

    from services.publishing_adapters_db import PublishingAdapterRow
    return PublishingAdapterRow(
        id=uuid4(),
        name=name or f"{platform}_main",
        platform=platform,
        handler_name=handler_name or platform,
        credentials_ref=f"{platform}_",
        enabled=True,
        config={},
        metadata={},
    )


class TestDistributeToAdapters:
    """Verify ``_distribute_to_adapters`` walks DB-loaded rows + is crash-safe.

    All tests patch :func:`services.publishing_adapters_db.load_enabled_publishers`
    rather than hitting a real DB, and patch the per-handler module's
    imported adapter symbol (``services.integrations.handlers.publishing_*``)
    rather than ``services.social_adapters.*`` — the handler imports those
    names at module load, so patching the source module wouldn't intercept.
    """

    @pytest.mark.asyncio
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_empty_enabled_returns_empty(self, mock_load):
        """Legacy ``enabled=set()`` with NO DB rows returns empty + logs INFO."""
        import logging

        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = []
        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]

        with patch("services.social_poster.logger") as mock_logger:
            result = await _distribute_to_adapters(posts, set())

        assert result == {}
        mock_logger.info.assert_any_call(
            "[social_poster] publishing dispatch: no enabled adapters"
        )

    @pytest.mark.asyncio
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_no_posts_returns_empty(self, mock_load):
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky"), _row("mastodon")]
        result = await _distribute_to_adapters([], {"bluesky", "mastodon"})
        assert result == {}

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_walks_db_loaded_rows(self, mock_load, mock_bsky, mock_masto):
        """The dispatcher loops over DB-loaded rows, not the legacy set."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky"), _row("mastodon")]
        mock_bsky.return_value = {"success": True, "post_id": "bsky1", "error": None}
        mock_masto.return_value = {"success": True, "post_id": "masto1", "error": None}

        posts = [SocialPost(platform="twitter", text="hello", post_url="https://x.com/1")]
        result = await _distribute_to_adapters(posts, {"bluesky", "mastodon"})

        assert result["bluesky"]["success"] is True
        assert result["mastodon"]["success"] is True
        mock_bsky.assert_awaited_once()
        mock_masto.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_adapters_receive_site_config_kwarg(self, mock_load, mock_bsky):
        """REGRESSION (poindexter#112): every dispatched call receives
        ``site_config=`` so the bluesky/mastodon adapters' DI gate doesn't
        misfire. The 30-day distribution-dark bug (fixed 2026-05-09 17:00 UTC)
        was caused by a lambda dropping the kwarg — pin it forever."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {"success": True, "post_id": "x", "error": None}

        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]
        await _distribute_to_adapters(posts, {"bluesky"})

        mock_bsky.assert_awaited_once()
        kwargs = mock_bsky.await_args.kwargs
        assert "site_config" in kwargs
        assert kwargs["site_config"] is not None

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_mastodon.post_to_mastodon",
           new_callable=AsyncMock)
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_one_adapter_crash_does_not_kill_others(
        self, mock_load, mock_bsky, mock_masto,
    ):
        """GH-36 AC#5 / poindexter#112: graceful degradation must survive
        the registry-driven dispatch path too."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky"), _row("mastodon")]
        mock_bsky.side_effect = Exception("Bluesky is down")
        mock_masto.return_value = {"success": True, "post_id": "masto1", "error": None}

        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]
        result = await _distribute_to_adapters(posts, {"bluesky", "mastodon"})

        assert result["bluesky"]["success"] is False
        assert "Bluesky is down" in result["bluesky"]["error"]
        assert result["mastodon"]["success"] is True

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_advisory_filter_skips_unrowed_platforms(
        self, mock_load, mock_bsky, caplog,
    ):
        """Legacy ``enabled`` set with a platform that has no DB row =
        WARN + skip. No more silent no-op for stale configs."""
        import logging

        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {"success": True, "post_id": "b1", "error": None}

        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]
        with caplog.at_level(logging.WARNING, logger="services.social_poster"):
            result = await _distribute_to_adapters(posts, {"bluesky", "linkedin"})

        assert result["bluesky"]["success"] is True
        assert "linkedin" not in result
        assert any("linkedin" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_missing_credentials_logs_clean_skip(
        self, mock_load, mock_bsky, caplog,
    ):
        """GH-36 AC#7: missing credentials short-circuits with a clear log."""
        import logging

        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {
            "success": False,
            "post_id": None,
            "error": "bluesky_identifier or bluesky_app_password not configured",
        }

        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]
        with caplog.at_level(logging.WARNING, logger="services.social_poster"):
            result = await _distribute_to_adapters(posts, {"bluesky"})

        assert result["bluesky"]["success"] is False
        assert "not configured" in result["bluesky"]["error"]

    @pytest.mark.asyncio
    @patch("services.integrations.handlers.publishing_bluesky.post_to_bluesky",
           new_callable=AsyncMock)
    @patch("services.social_poster.load_enabled_publishers", new_callable=AsyncMock)
    async def test_counter_update_fires_after_dispatch(self, mock_load, mock_bsky):
        """Per-row counter writes mirror tap_runner — ``_record_publisher_outcome``
        is invoked after each adapter call when a pool is provided."""
        from services.social_poster import _distribute_to_adapters

        mock_load.return_value = [_row("bluesky")]
        mock_bsky.return_value = {"success": True, "post_id": "b1", "error": None}

        # Fake pool that records UPDATE statements.
        class _Conn:
            def __init__(self, parent): self.parent = parent
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, q, *args):
                self.parent.executes.append((q, args))
                return "UPDATE 1"

        class _Pool:
            def __init__(self): self.executes = []
            def acquire(self): return _Conn(self)

        pool = _Pool()
        posts = [SocialPost(platform="twitter", text="hi", post_url="https://x.com/1")]
        await _distribute_to_adapters(posts, {"bluesky"}, pool=pool)

        assert any("UPDATE publishing_adapters" in q for q, _ in pool.executes)


class TestBumpMetricNeverRaises:
    """The metrics shim is best-effort — even a broken Prometheus shouldn't
    take down social posting."""

    def test_no_labels_does_not_raise(self):
        from services.social_poster import _bump_metric

        # Should not raise even though we pass an unknown metric name.
        _bump_metric("definitely_unknown_metric")

    def test_known_metric_no_raise(self):
        from services.social_poster import _bump_metric

        _bump_metric("social_adapter_posts_total", platform="bluesky", outcome="success")
        _bump_metric("social_adapter_errors_total", platform="bluesky")
