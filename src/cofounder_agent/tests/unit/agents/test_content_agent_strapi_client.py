"""
Unit tests for agents/content_agent/services/strapi_client.py

Tests for StrapiClient (no real HTTP calls — all mocked).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strapi_client(api_url="https://strapi.example.com", api_token="test-token-12345"):
    """Build a StrapiClient with config mocked to provide env values."""
    with patch("agents.content_agent.services.strapi_client.config") as mock_cfg:
        mock_cfg.STRAPI_API_URL = api_url
        mock_cfg.STRAPI_API_TOKEN = api_token
        from agents.content_agent.services.strapi_client import StrapiClient
        client = StrapiClient()
    return client


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestStrapiClientInit:
    def test_raises_when_no_url(self):
        with patch("agents.content_agent.services.strapi_client.config") as mock_cfg:
            mock_cfg.STRAPI_API_URL = None
            mock_cfg.STRAPI_API_TOKEN = "token"
            from agents.content_agent.services.strapi_client import StrapiClient
            with pytest.raises(ValueError, match="STRAPI_API_URL"):
                StrapiClient()

    def test_raises_when_no_token(self):
        with patch("agents.content_agent.services.strapi_client.config") as mock_cfg:
            mock_cfg.STRAPI_API_URL = "https://strapi.example.com"
            mock_cfg.STRAPI_API_TOKEN = None
            from agents.content_agent.services.strapi_client import StrapiClient
            with pytest.raises(ValueError, match="STRAPI_API_TOKEN"):
                StrapiClient()

    def test_stores_api_url(self):
        client = _make_strapi_client(api_url="https://my-strapi.com")
        assert client.api_url == "https://my-strapi.com"

    def test_authorization_header_set(self):
        client = _make_strapi_client(api_token="my-jwt-token")
        assert client.headers["Authorization"] == "Bearer my-jwt-token"

    def test_logs_info_with_token_preview(self):
        with patch("agents.content_agent.services.strapi_client.config") as mock_cfg, \
             patch("agents.content_agent.services.strapi_client.logger") as mock_logger:
            mock_cfg.STRAPI_API_URL = "https://strapi.example.com"
            mock_cfg.STRAPI_API_TOKEN = "abcdefghijklmnop"
            from agents.content_agent.services.strapi_client import StrapiClient
            StrapiClient()
            mock_logger.info.assert_called()


# ---------------------------------------------------------------------------
# upload_image
# ---------------------------------------------------------------------------


class TestUploadImage:
    @pytest.mark.asyncio
    async def test_returns_image_id_on_success(self, tmp_path):
        client = _make_strapi_client()

        # Create a real temp file so open() works
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"fake-image-data")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [{"id": 42}]

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http):
            result = await client.upload_image(str(img_file), "alt text", "caption")

        assert result == 42

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self, tmp_path):
        import httpx

        client = _make_strapi_client()

        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"fake")

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.HTTPError("upload failed"))
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http):
            result = await client.upload_image(str(img_file), "alt", "cap")

        assert result is None

    @pytest.mark.asyncio
    async def test_logs_error_on_failure(self, tmp_path):
        import httpx

        client = _make_strapi_client()
        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"fake")

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=httpx.HTTPError("error"))
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http), \
             patch("agents.content_agent.services.strapi_client.logger") as mock_logger:
            await client.upload_image(str(img_file), "alt", "cap")
            mock_logger.error.assert_called()


# ---------------------------------------------------------------------------
# _make_request
# ---------------------------------------------------------------------------


class TestMakeRequest:
    @pytest.mark.asyncio
    async def test_get_request_returns_json(self):
        client = _make_strapi_client()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": 1}]}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http):
            result = await client._make_request("GET", "/posts")

        assert result == {"data": [{"id": 1}]}

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        import httpx

        client = _make_strapi_client()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.HTTPError("not found"))
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http):
            result = await client._make_request("GET", "/posts")

        assert result is None

    @pytest.mark.asyncio
    async def test_post_request_returns_json(self):
        client = _make_strapi_client()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"data": {"id": 5}}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        with patch("agents.content_agent.services.strapi_client._get_client", return_value=mock_http):
            result = await client._make_request("POST", "/posts", data={"title": "Test"})

        assert result == {"data": {"id": 5}}
