"""
Unit tests for agents/content_agent/services/pexels_client.py

Tests for PexelsClient (no real HTTP calls — all mocked).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.content_agent.services.pexels_client import PexelsClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(api_key: str = "test-pexels-key") -> PexelsClient:
    with patch("agents.content_agent.services.pexels_client.config") as mock_cfg:
        mock_cfg.PEXELS_API_KEY = api_key
        client = PexelsClient()
    return client


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestPexelsClientInit:
    def test_init_with_valid_key(self):
        client = _make_client("my-key-123")
        assert client.headers["Authorization"] == "my-key-123"

    def test_raises_when_no_api_key(self):
        with patch("agents.content_agent.services.pexels_client.config") as mock_cfg:
            mock_cfg.PEXELS_API_KEY = None
            with pytest.raises(ValueError, match="PEXELS_API_KEY"):
                PexelsClient()

    def test_base_url_defined(self):
        assert PexelsClient.BASE_URL == "https://api.pexels.com/v1/search"


# ---------------------------------------------------------------------------
# search_and_download
# ---------------------------------------------------------------------------


class TestSearchAndDownload:
    def _make_search_response(self, photos=None):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"photos": photos or []}
        return mock_resp

    def _make_image_response(self, content=b"fake-image-bytes"):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = content
        return mock_resp

    @pytest.mark.asyncio
    async def test_returns_true_on_successful_download(self, tmp_path):
        client = _make_client()
        photo_data = {"photos": [{"src": {"large": "https://images.pexels.com/photo.jpg"}}]}
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = photo_data
        image_resp = self._make_image_response(b"image-bytes")

        with patch("agents.content_agent.services.pexels_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[search_resp, image_resp])
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            out_path = str(tmp_path / "image.jpg")
            result = await client.search_and_download("sunset", out_path)

        assert result is True

    @pytest.mark.asyncio
    async def test_writes_image_file_on_success(self, tmp_path):
        client = _make_client()
        photo_data = {"photos": [{"src": {"large": "https://images.pexels.com/photo.jpg"}}]}
        search_resp = MagicMock()
        search_resp.raise_for_status = MagicMock()
        search_resp.json.return_value = photo_data
        image_resp = self._make_image_response(b"PNG-BYTES")

        out_path = str(tmp_path / "output.jpg")

        with patch("agents.content_agent.services.pexels_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[search_resp, image_resp])
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            await client.search_and_download("cats", out_path)

        assert (tmp_path / "output.jpg").read_bytes() == b"PNG-BYTES"

    @pytest.mark.asyncio
    async def test_returns_false_when_no_photos(self, tmp_path):
        client = _make_client()
        search_resp = self._make_search_response(photos=[])

        with patch("agents.content_agent.services.pexels_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=search_resp)
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.search_and_download("query", str(tmp_path / "out.jpg"))

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self, tmp_path):
        import httpx

        client = _make_client()

        with patch("agents.content_agent.services.pexels_client.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=httpx.HTTPError("connection refused"))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await client.search_and_download("query", str(tmp_path / "out.jpg"))

        assert result is False
