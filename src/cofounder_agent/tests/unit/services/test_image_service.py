"""
Unit tests for services/image_service.py

Tests FeaturedImageMetadata (to_dict, to_markdown), ImageService initialization,
search_featured_image, get_images_for_gallery, _pexels_search (mocked httpx),
generate_image_markdown, optimize_image_for_web, cache helpers, and factory.
Heavy GPU/SDXL paths are not exercised; they are tested via flag checks only.
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_service import (
    FeaturedImageMetadata,
    ImageService,
    get_image_service,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_PHOTO = {
    "src": {
        "large": "https://pexels.com/photo/large.jpg",
        "small": "https://pexels.com/photo/small.jpg",
    },
    "photographer": "Jane Doe",
    "photographer_url": "https://pexels.com/@jane",
    "width": 1920,
    "height": 1080,
    "alt": "A beautiful landscape",
}


def make_mock_httpx_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


@asynccontextmanager
async def mock_async_client(response):
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    yield client


def make_image_service_with_key() -> ImageService:
    """Return an ImageService with a fake Pexels API key injected."""
    with patch.dict("os.environ", {"PEXELS_API_KEY": "fake-pexels-key"}):
        return ImageService()


def make_image_service_no_key() -> ImageService:
    """Return an ImageService without Pexels API key."""
    with patch.dict("os.environ", {}, clear=True):
        import os
        os.environ.pop("PEXELS_API_KEY", None)
        return ImageService()


# ---------------------------------------------------------------------------
# FeaturedImageMetadata
# ---------------------------------------------------------------------------


class TestFeaturedImageMetadata:
    def _make_meta(self, **kwargs) -> FeaturedImageMetadata:
        defaults = dict(
            url="https://example.com/photo.jpg",
            thumbnail="https://example.com/thumb.jpg",
            photographer="John Smith",
            photographer_url="https://example.com/@john",
            width=1920,
            height=1080,
            alt_text="A photo",
            caption="Photo caption",
            source="pexels",
            search_query="nature",
        )
        defaults.update(kwargs)
        return FeaturedImageMetadata(**defaults)  # type: ignore[arg-type]

    def test_to_dict_contains_url(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert d["url"] == "https://example.com/photo.jpg"

    def test_to_dict_contains_photographer(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert d["photographer"] == "John Smith"

    def test_to_dict_contains_source(self):
        meta = self._make_meta()
        assert meta.to_dict()["source"] == "pexels"

    def test_to_dict_contains_retrieved_at(self):
        meta = self._make_meta()
        d = meta.to_dict()
        assert "retrieved_at" in d

    def test_thumbnail_falls_back_to_url(self):
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg")
        assert meta.thumbnail == "https://example.com/photo.jpg"

    def test_to_markdown_contains_url(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "https://example.com/photo.jpg" in md

    def test_to_markdown_includes_photographer(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "John Smith" in md

    def test_to_markdown_includes_photographer_link_when_url_set(self):
        meta = self._make_meta()
        md = meta.to_markdown()
        assert "[John Smith](https://example.com/@john)" in md

    def test_to_markdown_caption_override(self):
        meta = self._make_meta()
        md = meta.to_markdown(caption_override="My Custom Caption")
        assert "My Custom Caption" in md

    def test_to_markdown_falls_back_to_alt_text(self):
        meta = self._make_meta(caption="", alt_text="alt text description")
        md = meta.to_markdown()
        assert "alt text description" in md


# ---------------------------------------------------------------------------
# ImageService.__init__
# ---------------------------------------------------------------------------


class TestImageServiceInit:
    def test_pexels_available_with_key(self):
        svc = make_image_service_with_key()
        assert svc.pexels_available is True

    def test_pexels_not_available_without_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService()
        assert svc.pexels_available is False

    def test_pexels_base_url_set(self):
        svc = ImageService()
        assert "pexels.com" in svc.pexels_base_url

    def test_sdxl_not_initialized_at_startup(self):
        svc = ImageService()
        # SDXL is lazily initialized only when generate_image() is called
        assert svc.sdxl_initialized is False
        assert svc.sdxl_pipe is None

    def test_search_cache_starts_empty(self):
        svc = ImageService()
        assert svc.search_cache == {}


# ---------------------------------------------------------------------------
# search_featured_image
# ---------------------------------------------------------------------------


class TestSearchFeaturedImage:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService()
        result = await svc.search_featured_image("AI")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_image_metadata_on_success(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.search_featured_image("nature")

        assert result is not None
        assert isinstance(result, FeaturedImageMetadata)
        assert result.url == SAMPLE_PHOTO["src"]["large"]

    @pytest.mark.asyncio
    async def test_returns_none_when_no_photos_found(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": []})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.search_featured_image("very_obscure_topic_xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_excludes_person_keywords(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        captured_queries = []

        async def capture_pexels_search(query, **kwargs):
            captured_queries.append(query)
            return []

        with patch.object(svc, "_pexels_search", side_effect=capture_pexels_search):
            await svc.search_featured_image("AI", keywords=["portrait", "people", "technology"])

        # "portrait" and "people" should be excluded; "technology" should be included
        assert not any("portrait" in q for q in captured_queries)
        assert not any("people" in q for q in captured_queries)


# ---------------------------------------------------------------------------
# get_images_for_gallery
# ---------------------------------------------------------------------------


class TestGetImagesForGallery:
    @pytest.mark.asyncio
    async def test_returns_empty_list_without_api_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService()
        result = await svc.get_images_for_gallery("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_images_up_to_count(self):
        svc = make_image_service_with_key()
        # Two photos returned by pexels search
        photos = [SAMPLE_PHOTO, {**SAMPLE_PHOTO, "src": {"large": "url2", "small": "url2s"}}]
        resp = make_mock_httpx_response({"photos": photos})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc.get_images_for_gallery("nature", count=2)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_list_on_api_error(self):
        svc = make_image_service_with_key()
        with patch.object(svc, "_pexels_search", side_effect=Exception("API down")):
            result = await svc.get_images_for_gallery("AI")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _pexels_search
# ---------------------------------------------------------------------------


class TestPexelsSearch:
    @pytest.mark.asyncio
    async def test_returns_empty_list_without_key(self, monkeypatch):
        monkeypatch.delenv("PEXELS_API_KEY", raising=False)
        svc = ImageService()
        result = await svc._pexels_search("AI")
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_photos_to_metadata(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc._pexels_search("nature")

        assert len(result) == 1
        img = result[0]
        assert img.photographer == "Jane Doe"
        assert img.width == 1920
        assert img.height == 1080

    @pytest.mark.asyncio
    async def test_source_is_pexels(self):
        svc = make_image_service_with_key()
        resp = make_mock_httpx_response({"photos": [SAMPLE_PHOTO]})
        with patch("httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.get = AsyncMock(return_value=resp)
            mock_cls.return_value = mock_ctx

            result = await svc._pexels_search("nature")

        assert result[0].source == "pexels"


# ---------------------------------------------------------------------------
# generate_image_markdown / optimize_image_for_web / cache helpers
# ---------------------------------------------------------------------------


class TestImageServiceUtils:
    def test_generate_image_markdown_delegates_to_metadata(self):
        svc = ImageService()
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg", photographer="John")
        md = svc.generate_image_markdown(meta, caption="Custom caption")
        assert "Custom caption" in md
        assert "example.com/photo.jpg" in md

    @pytest.mark.asyncio
    async def test_optimize_image_returns_original_url(self):
        svc = ImageService()
        result = await svc.optimize_image_for_web("https://example.com/image.jpg")
        assert result is not None
        assert result["url"] == "https://example.com/image.jpg"
        assert result["optimized"] is False

    def test_cache_get_returns_none_when_empty(self):
        svc = ImageService()
        assert svc.get_search_cache("any_query") is None

    def test_cache_set_and_get(self):
        svc = ImageService()
        meta = FeaturedImageMetadata(url="https://example.com/photo.jpg")
        svc.set_search_cache("nature", [meta])
        cached = svc.get_search_cache("nature")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].url == "https://example.com/photo.jpg"


# ---------------------------------------------------------------------------
# get_image_service factory
# ---------------------------------------------------------------------------


class TestGetImageServiceFactory:
    def test_returns_image_service_instance(self):
        svc = get_image_service()
        assert isinstance(svc, ImageService)

    def test_returns_fresh_instance_each_time(self):
        s1 = get_image_service()
        s2 = get_image_service()
        assert s1 is not s2
