"""
Unit tests for services/pexels_client.py

Tests PexelsClient image search, content filtering, gallery building,
and markdown generation. No real HTTP calls are made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.pexels_client import PexelsClient, get_pexels_client


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def make_photo(
    alt: str = "A beautiful landscape",
    photographer: str = "Jane Doe",
    photographer_url: str = "https://pexels.com/photographer/janedoe",
    width: int = 1920,
    height: int = 1080,
) -> dict:
    return {
        "id": 12345,
        "src": {
            "large": "https://images.pexels.com/photos/12345/large.jpg",
            "small": "https://images.pexels.com/photos/12345/small.jpg",
        },
        "photographer": photographer,
        "photographer_url": photographer_url,
        "width": width,
        "height": height,
        "alt": alt,
    }


def make_mock_response(photos: list, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = {"photos": photos}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestPexelsClientInit:
    def test_uses_env_api_key(self, monkeypatch):
        monkeypatch.setenv("PEXELS_API_KEY", "env-pexels-key")
        client = PexelsClient()
        assert client.api_key == "env-pexels-key"

    def test_explicit_api_key(self):
        client = PexelsClient(api_key="explicit-key")
        assert client.api_key == "explicit-key"

    def test_no_api_key_sets_empty_headers(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        client = PexelsClient()
        assert client.api_key is None
        assert client.headers == {}

    def test_with_api_key_sets_authorization_header(self):
        client = PexelsClient(api_key="my-key")
        assert client.headers == {"Authorization": "my-key"}


# ---------------------------------------------------------------------------
# Content filtering
# ---------------------------------------------------------------------------


class TestContentFiltering:
    def test_appropriate_image_passes(self):
        client = PexelsClient(api_key="key")
        photo = make_photo(alt="mountain landscape", photographer="Bob")
        assert client._is_content_appropriate(photo) is True

    def test_nsfw_in_alt_blocked(self):
        client = PexelsClient(api_key="key")
        photo = make_photo(alt="nsfw beach scene")
        assert client._is_content_appropriate(photo) is False

    def test_inappropriate_photographer_name_blocked(self):
        client = PexelsClient(api_key="key")
        photo = make_photo(alt="portrait", photographer="nude artist")
        assert client._is_content_appropriate(photo) is False

    def test_case_insensitive_filtering(self):
        client = PexelsClient(api_key="key")
        photo = make_photo(alt="SEXY outfit photo")
        assert client._is_content_appropriate(photo) is False

    def test_none_alt_treated_as_empty(self):
        client = PexelsClient(api_key="key")
        photo = make_photo(alt=None)  # type: ignore[arg-type]
        # Should not raise; no inappropriate words → appropriate
        result = client._is_content_appropriate(photo)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# search_images
# ---------------------------------------------------------------------------


class TestSearchImages:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        client = PexelsClient()
        result = await client.search_images("landscape")
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_search_returns_images(self):
        client = PexelsClient(api_key="key")
        photos = [make_photo()]
        mock_resp = make_mock_response(photos)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await client.search_images("mountains", per_page=1)

        assert len(result) == 1
        assert result[0]["source"] == "pexels"
        assert "url" in result[0]
        assert "photographer" in result[0]

    @pytest.mark.asyncio
    async def test_inappropriate_photos_filtered_out(self):
        client = PexelsClient(api_key="key")
        appropriate = make_photo(alt="office desk")
        inappropriate = make_photo(alt="nsfw content")
        mock_resp = make_mock_response([appropriate, inappropriate])

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await client.search_images("office", per_page=2)

        assert len(result) == 1
        assert result[0]["alt"] == "office desk"

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        client = PexelsClient(api_key="key")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(side_effect=Exception("network error"))
            mock_cls.return_value = mock_ctx

            result = await client.search_images("test")
        assert result == []

    @pytest.mark.asyncio
    async def test_result_structure(self):
        client = PexelsClient(api_key="key")
        photo = make_photo()
        mock_resp = make_mock_response([photo])

        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_ctx

            result = await client.search_images("test")

        img = result[0]
        assert "url" in img
        assert "thumbnail" in img
        assert "photographer" in img
        assert "photographer_url" in img
        assert "width" in img
        assert "height" in img
        assert "alt" in img
        assert img["searched_query"] == "test"


# ---------------------------------------------------------------------------
# get_featured_image
# ---------------------------------------------------------------------------


class TestGetFeaturedImage:
    @pytest.mark.asyncio
    async def test_returns_first_image(self):
        client = PexelsClient(api_key="key")
        expected = {"url": "https://example.com/img.jpg", "source": "pexels"}

        with patch.object(
            client, "search_images", new=AsyncMock(return_value=[expected])
        ):
            result = await client.get_featured_image("AI landscape")
        assert result is expected

    @pytest.mark.asyncio
    async def test_tries_keywords_if_topic_fails(self):
        client = PexelsClient(api_key="key")
        expected = {"url": "https://example.com/img.jpg"}
        calls = []

        async def mock_search(query, per_page=1):
            calls.append(query)
            if query == "technology":
                return [expected]
            return []

        with patch.object(client, "search_images", side_effect=mock_search):
            result = await client.get_featured_image("abstract", keywords=["technology"])

        assert result is expected
        assert "technology" in calls

    @pytest.mark.asyncio
    async def test_returns_none_if_no_images_found(self):
        client = PexelsClient(api_key="key")
        with patch.object(client, "search_images", new=AsyncMock(return_value=[])):
            result = await client.get_featured_image("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# get_images_for_gallery
# ---------------------------------------------------------------------------


class TestGetImagesForGallery:
    @pytest.mark.asyncio
    async def test_returns_up_to_count_images(self):
        client = PexelsClient(api_key="key")
        photos = [{"url": f"https://img.com/{i}.jpg"} for i in range(5)]

        with patch.object(client, "search_images", new=AsyncMock(return_value=photos)):
            result = await client.get_images_for_gallery("nature", count=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_combines_results_from_multiple_queries(self):
        client = PexelsClient(api_key="key")
        call_count = 0

        async def mock_search(query, per_page=5):
            nonlocal call_count
            call_count += 1
            return [{"url": f"img-{query}-{call_count}.jpg"}]

        with patch.object(client, "search_images", side_effect=mock_search):
            result = await client.get_images_for_gallery(
                "ocean", count=3, keywords=["waves", "beach"]
            )
        # Should have searched multiple queries
        assert call_count >= 2


# ---------------------------------------------------------------------------
# generate_image_markdown
# ---------------------------------------------------------------------------


class TestGenerateImageMarkdown:
    def test_with_photographer_url(self):
        img = {
            "url": "https://img.com/photo.jpg",
            "photographer": "John Smith",
            "photographer_url": "https://pexels.com/john",
            "alt": "Beautiful sunset",
        }
        md = PexelsClient.generate_image_markdown(img, caption="Sunset")
        assert "![Sunset](https://img.com/photo.jpg)" in md
        assert "[John Smith](https://pexels.com/john)" in md
        assert "Pexels" in md

    def test_without_photographer_url(self):
        img = {
            "url": "https://img.com/photo.jpg",
            "photographer": "Jane Doe",
            "photographer_url": "",
            "alt": "Mountain view",
        }
        md = PexelsClient.generate_image_markdown(img)
        assert "Jane Doe" in md
        # Without URL, photographer name appears as plain text
        assert "[Jane Doe]" not in md

    def test_uses_alt_as_default_caption(self):
        img = {
            "url": "https://img.com/photo.jpg",
            "photographer": "Bob",
            "photographer_url": "",
            "alt": "City skyline",
        }
        md = PexelsClient.generate_image_markdown(img)
        assert "City skyline" in md


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


class TestGetPexelsClient:
    def test_returns_pexels_instance(self, monkeypatch):
        monkeypatch.setenv("PEXELS_API_KEY", "factory-key")
        client = get_pexels_client()
        assert isinstance(client, PexelsClient)
        assert client.api_key == "factory-key"
