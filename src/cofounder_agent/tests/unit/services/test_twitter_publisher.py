"""
Unit tests for services/twitter_publisher.py

Tests TwitterPublisher initialization, single tweet publish, and thread publish.
HTTP calls are mocked to avoid real API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.twitter_publisher import TwitterPublisher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unconfigured_publisher(monkeypatch) -> TwitterPublisher:
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    return TwitterPublisher()


@pytest.fixture
def configured_publisher(monkeypatch) -> TwitterPublisher:
    monkeypatch.setenv("TWITTER_BEARER_TOKEN", "test-bearer-token")
    return TwitterPublisher()


def make_mock_response(status_code: int, data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = str(data)
    return resp


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestTwitterPublisherInit:
    def test_available_when_configured(self, configured_publisher):
        assert configured_publisher.available is True

    def test_unavailable_when_not_configured(self, unconfigured_publisher):
        assert unconfigured_publisher.available is False

    def test_explicit_bearer_token(self):
        pub = TwitterPublisher(bearer_token="my-token")
        assert pub.bearer_token == "my-token"
        assert pub.available is True

    def test_env_bearer_token_used(self, monkeypatch):
        monkeypatch.setenv("TWITTER_BEARER_TOKEN", "env-token")
        pub = TwitterPublisher()
        assert pub.bearer_token == "env-token"


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


class TestTwitterPublisherPublish:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.publish(text="Hello world")
        assert result["success"] is False
        assert "not configured" in result["error"].lower()
        assert result["tweet_id"] is None

    @pytest.mark.asyncio
    async def test_successful_tweet(self, configured_publisher):
        mock_resp = make_mock_response(201, {"data": {"id": "tweet-123", "text": "Hello"}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(text="Hello Twitter world!")

        assert result["success"] is True
        assert result["tweet_id"] == "tweet-123"
        assert "twitter.com" in result["url"]
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_text_truncated_to_280_chars(self, configured_publisher):
        long_text = "A" * 300
        mock_resp = make_mock_response(201, {"data": {"id": "abc", "text": "..."}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish(text=long_text)

        call_kwargs = mock_ctx.post.call_args[1]
        assert len(call_kwargs["json"]["text"]) == 280
        assert call_kwargs["json"]["text"].endswith("...")

    @pytest.mark.asyncio
    async def test_short_text_not_truncated(self, configured_publisher):
        short_text = "Short tweet"
        mock_resp = make_mock_response(201, {"data": {"id": "abc", "text": short_text}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish(text=short_text)

        call_kwargs = mock_ctx.post.call_args[1]
        assert call_kwargs["json"]["text"] == short_text

    @pytest.mark.asyncio
    async def test_reply_to_id_included_in_payload(self, configured_publisher):
        mock_resp = make_mock_response(201, {"data": {"id": "reply-tweet"}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish(text="Reply tweet!", reply_to_id="original-tweet")

        call_kwargs = mock_ctx.post.call_args[1]
        assert call_kwargs["json"]["reply"]["in_reply_to_tweet_id"] == "original-tweet"

    @pytest.mark.asyncio
    async def test_api_error_returns_failure(self, configured_publisher):
        mock_resp = make_mock_response(403, {"detail": "Forbidden access"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(text="Forbidden tweet")

        assert result["success"] is False
        assert "Forbidden access" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, configured_publisher):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("Network error"))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(text="Test tweet")

        assert result["success"] is False
        assert "Publishing error" in result["error"]


# ---------------------------------------------------------------------------
# publish_thread()
# ---------------------------------------------------------------------------


class TestTwitterPublisherThread:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.publish_thread(["tweet 1", "tweet 2"])
        assert result["success"] is False
        assert "not configured" in result["error"].lower()
        assert result["tweet_ids"] == []

    @pytest.mark.asyncio
    async def test_successful_thread(self, configured_publisher):
        responses = [
            make_mock_response(201, {"data": {"id": "tweet-1"}}),
            make_mock_response(201, {"data": {"id": "tweet-2"}}),
        ]

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=responses)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish_thread(
                ["First tweet", "Second tweet"]
            )

        assert result["success"] is True
        assert result["tweet_ids"] == ["tweet-1", "tweet-2"]
        assert len(result["urls"]) == 2

    @pytest.mark.asyncio
    async def test_thread_second_tweet_replies_to_first(self, configured_publisher):
        responses = [
            make_mock_response(201, {"data": {"id": "tweet-1"}}),
            make_mock_response(201, {"data": {"id": "tweet-2"}}),
        ]
        call_payloads = []

        async def capture_post(*args, **kwargs):
            call_payloads.append(kwargs.get("json", {}))
            return responses.pop(0)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=capture_post)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish_thread(["First", "Second"])

        # Second tweet should have reply referencing first tweet
        assert "reply" in call_payloads[1]
        assert call_payloads[1]["reply"]["in_reply_to_tweet_id"] == "tweet-1"

    @pytest.mark.asyncio
    async def test_thread_partial_failure(self, configured_publisher):
        responses = [
            make_mock_response(201, {"data": {"id": "tweet-1"}}),
            make_mock_response(403, {"detail": "Rate limited"}),
        ]

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=responses)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish_thread(
                ["First tweet", "Second tweet"]
            )

        assert result["success"] is False
        # First tweet was published
        assert "tweet-1" in result["tweet_ids"]

    @pytest.mark.asyncio
    async def test_thread_exception_returns_failure(self, configured_publisher):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(side_effect=Exception("API down"))
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish_thread(["Tweet 1"])

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_thread_truncates_long_tweets(self, configured_publisher):
        long_tweet = "X" * 300
        mock_resp = make_mock_response(201, {"data": {"id": "tweet-1"}})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish_thread([long_tweet])

        call_kwargs = mock_ctx.post.call_args[1]
        assert len(call_kwargs["json"]["text"]) == 280
