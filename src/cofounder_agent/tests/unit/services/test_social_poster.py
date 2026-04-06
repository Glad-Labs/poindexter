"""Unit tests for social_poster.py — social media post generation and distribution."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from services.social_poster import (
    SocialPost,
    _build_twitter_prompt,
    _build_linkedin_prompt,
    _generate_social_text,
    generate_social_posts,
    generate_and_distribute_social_posts,
    TWITTER_CHAR_LIMIT,
    LINKEDIN_CHAR_LIMIT,
)


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
    @patch("services.social_poster.httpx.AsyncClient")
    async def test_sends_telegram_and_discord(self, mock_client_cls):
        from services.social_poster import _notify

        mock_client = AsyncMock()
        mock_client.post.return_value = MagicMock(status_code=200)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        await _notify("Test notification message")

        assert mock_client.post.call_count == 2
        # First call: Telegram
        telegram_call = mock_client.post.call_args_list[0]
        assert "api.telegram.org" in telegram_call.args[0]
        assert telegram_call.kwargs["json"]["text"] == "Test notification message"
        # Second call: Discord via OpenClaw
        discord_call = mock_client.post.call_args_list[1]
        assert "/hooks/agent" in discord_call.args[0]

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
