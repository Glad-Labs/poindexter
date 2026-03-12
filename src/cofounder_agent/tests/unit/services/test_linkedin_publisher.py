"""
Unit tests for services/linkedin_publisher.py

Tests LinkedInPublisher initialization, publish, and schedule.
HTTP calls are mocked to avoid real API calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.linkedin_publisher import LinkedInPublisher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def unconfigured_publisher(monkeypatch) -> LinkedInPublisher:
    monkeypatch.delenv("LINKEDIN_ACCESS_TOKEN", raising=False)
    return LinkedInPublisher()


@pytest.fixture
def configured_publisher(monkeypatch) -> LinkedInPublisher:
    monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "test-linkedin-token")
    return LinkedInPublisher()


def make_mock_response(status_code: int, data: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = str(data)
    return resp


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestLinkedInPublisherInit:
    def test_available_true_when_configured(self, configured_publisher):
        assert configured_publisher.available is True

    def test_available_false_when_unconfigured(self, unconfigured_publisher):
        assert unconfigured_publisher.available is False

    def test_explicit_access_token(self):
        publisher = LinkedInPublisher(access_token="explicit-token")
        assert publisher.access_token == "explicit-token"
        assert publisher.available is True

    def test_env_access_token_used(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_ACCESS_TOKEN", "env-token")
        publisher = LinkedInPublisher()
        assert publisher.access_token == "env-token"


# ---------------------------------------------------------------------------
# publish()
# ---------------------------------------------------------------------------


class TestLinkedInPublisherPublish:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.publish(
            title="Test Post", content="Content"
        )
        assert result["success"] is False
        assert "not configured" in result["error"].lower()
        assert result["post_id"] is None

    @pytest.mark.asyncio
    async def test_successful_publish(self, configured_publisher):
        mock_resp = make_mock_response(201, {"id": "urn:li:share:1234567"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(
                title="AI Trends 2025", content="Here are the top AI trends..."
            )

        assert result["success"] is True
        assert result["post_id"] == "urn:li:share:1234567"
        assert "linkedin.com" in result["url"]
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_publish_with_image_url(self, configured_publisher):
        mock_resp = make_mock_response(201, {"id": "urn:li:share:9999"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(
                title="Post with image",
                content="Content",
                image_url="https://images.example.com/photo.jpg",
            )

        assert result["success"] is True
        # Verify that the image was included in the payload
        call_kwargs = mock_ctx.post.call_args[1]
        entities = call_kwargs["json"]["content"]["contentEntities"]
        assert len(entities) == 1

    @pytest.mark.asyncio
    async def test_api_error_returns_failure(self, configured_publisher):
        mock_resp = make_mock_response(403, {"message": "Forbidden"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(
                title="Forbidden Post", content="Content"
            )

        assert result["success"] is False
        assert "Forbidden" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_failure(self, configured_publisher):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=Exception("Network failure"))
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.publish(
                title="Test", content="Content"
            )

        assert result["success"] is False
        assert "Publishing error" in result["error"]

    @pytest.mark.asyncio
    async def test_content_truncated_to_3000_chars(self, configured_publisher):
        long_content = "x" * 5000
        mock_resp = make_mock_response(201, {"id": "abc"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish(title="Long Post", content=long_content)

        call_kwargs = mock_ctx.post.call_args[1]
        assert len(call_kwargs["json"]["text"]["text"]) == 3000

    @pytest.mark.asyncio
    async def test_title_truncated_to_200_chars(self, configured_publisher):
        long_title = "T" * 300
        mock_resp = make_mock_response(201, {"id": "abc"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            await configured_publisher.publish(title=long_title, content="Content")

        call_kwargs = mock_ctx.post.call_args[1]
        assert len(call_kwargs["json"]["content"]["title"]) == 200


# ---------------------------------------------------------------------------
# schedule()
# ---------------------------------------------------------------------------


class TestLinkedInPublisherSchedule:
    @pytest.mark.asyncio
    async def test_not_configured_returns_failure(self, unconfigured_publisher):
        result = await unconfigured_publisher.schedule(
            title="Future Post",
            content="Content",
            scheduled_time="2025-12-25T10:00:00Z",
        )
        assert result["success"] is False
        assert "not configured" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_successful_schedule(self, configured_publisher):
        mock_resp = make_mock_response(201, {"id": "scheduled-post-123"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.schedule(
                title="Holiday Post",
                content="Happy holidays!",
                scheduled_time="2025-12-25T10:00:00Z",
            )

        assert result["success"] is True
        assert result["scheduled"] is True

    @pytest.mark.asyncio
    async def test_schedule_api_error_returns_failure(self, configured_publisher):
        mock_resp = make_mock_response(400, {"message": "Bad scheduled time"})

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.schedule(
                title="Bad Post",
                content="Content",
                scheduled_time="invalid-datetime",
            )

        assert result["success"] is False
        assert result["scheduled"] is False

    @pytest.mark.asyncio
    async def test_schedule_exception_returns_failure(self, configured_publisher):
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = AsyncMock(side_effect=Exception("Timeout"))
            mock_cls.return_value = mock_ctx

            result = await configured_publisher.schedule(
                title="Test",
                content="Content",
                scheduled_time="2025-12-25T10:00:00Z",
            )

        assert result["success"] is False
