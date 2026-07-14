"""Unit tests for ``services/image_providers/pexels_video.py``.

Mocks ``httpx.AsyncClient`` + the container get_secret path. No real
Pexels API calls. Mirrors ``test_image_providers_pexels.py``'s shape —
same account, same secret, sibling search endpoint (video vs photo).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.video_provider import VideoResult
from services.image_providers.pexels_video import PexelsVideoProvider, _pick_video_file


def _make_pexels_client(videos: list[dict], status: int = 200):
    """Fake httpx.AsyncClient returning a Pexels ``/videos/search`` payload."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"videos": videos})
    client.get = AsyncMock(return_value=resp)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


_SAMPLE_VIDEO = {
    "id": 2499611,
    "width": 1920,
    "height": 1080,
    "duration": 12,
    "url": "https://www.pexels.com/video/2499611/",
    "user": {"name": "Jane Doe"},
    "video_files": [
        {
            "id": 1, "quality": "sd", "file_type": "video/mp4",
            "width": 426, "height": 240,
            "link": "https://videos.pexels.com/2499611/sd.mp4",
        },
        {
            "id": 2, "quality": "hd", "file_type": "video/mp4",
            "width": 1280, "height": 720,
            "link": "https://videos.pexels.com/2499611/hd.mp4",
        },
    ],
}


class TestPickVideoFile:
    """The size/quality-tradeoff selector — pure function, no I/O."""

    def test_prefers_hd_over_sd(self):
        picked = _pick_video_file(_SAMPLE_VIDEO["video_files"])
        assert picked is not None
        assert picked["quality"] == "hd"
        assert picked["link"] == "https://videos.pexels.com/2499611/hd.mp4"

    def test_falls_back_to_sd_when_no_hd(self):
        files = [f for f in _SAMPLE_VIDEO["video_files"] if f["quality"] != "hd"]
        picked = _pick_video_file(files)
        assert picked is not None
        assert picked["quality"] == "sd"

    def test_skips_non_mp4_file_types(self):
        files = [{"quality": "hd", "file_type": "video/webm", "link": "x.webm"}]
        assert _pick_video_file(files) is None

    def test_skips_entries_missing_link(self):
        files = [{"quality": "hd", "file_type": "video/mp4", "link": ""}]
        assert _pick_video_file(files) is None

    def test_empty_list_returns_none(self):
        assert _pick_video_file([]) is None

    def test_unrecognized_quality_label_still_picked(self):
        """Pexels occasionally ships a quality label outside hd/sd/uhd —
        degrade gracefully to whatever mp4 is available rather than
        dropping the whole video."""
        files = [{
            "quality": "4k", "file_type": "video/mp4",
            "link": "https://videos.pexels.com/x/4k.mp4",
        }]
        picked = _pick_video_file(files)
        assert picked is not None
        assert picked["link"] == "https://videos.pexels.com/x/4k.mp4"


class TestFetch:
    @pytest.mark.asyncio
    async def test_returns_video_results(self):
        ctx, _ = _make_pexels_client([_SAMPLE_VIDEO])
        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch(
                "server room", config={"api_key": "test-key"},
            )
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, VideoResult)
        assert r.file_url == "https://videos.pexels.com/2499611/hd.mp4"
        assert r.duration_s == 12
        assert r.width == 1280  # the CHOSEN file's dims, not the video's top-level dims
        assert r.height == 720
        assert r.format == "mp4"
        assert r.source == "pexels_video"
        assert r.prompt == "server room"
        assert r.metadata["pexels_id"] == 2499611

    @pytest.mark.asyncio
    async def test_video_with_no_usable_file_is_skipped(self):
        video = {**_SAMPLE_VIDEO, "video_files": [
            {"quality": "hd", "file_type": "video/webm", "link": "x.webm"},
        ]}
        ctx, _ = _make_pexels_client([video])
        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_empty_list(self):
        provider = PexelsVideoProvider()
        with patch("services.container.get_service", return_value=None):
            results = await provider.fetch("query", config={})
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        provider = PexelsVideoProvider()
        results = await provider.fetch("", config={"api_key": "x"})
        assert results == []
        results = await provider.fetch("   ", config={"api_key": "x"})
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_video_files_key_skipped(self):
        ctx, _ = _make_pexels_client([{"id": 1, "duration": 5}])  # no video_files
        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results == []

    @pytest.mark.asyncio
    async def test_non_dict_response_handled(self):
        client = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value="rate limited")
        client.get = AsyncMock(return_value=resp)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            results = await provider.fetch("q", config={"api_key": "k"})
        assert results == []

    @pytest.mark.asyncio
    async def test_params_sent_correctly(self):
        captured: dict[str, Any] = {}

        async def capture_get(url: str, headers: Any = None, params: Any = None):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"videos": []})
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture_get)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            await provider.fetch(
                "sunset",
                config={
                    "api_key": "secret-key",
                    "per_page": 3,
                    "orientation": "portrait",
                    "page": 2,
                },
            )

        assert "pexels.com" in captured["url"]
        assert "/videos/search" in captured["url"]
        assert captured["headers"]["Authorization"] == "secret-key"
        assert captured["params"]["query"] == "sunset"
        assert captured["params"]["per_page"] == 3
        assert captured["params"]["orientation"] == "portrait"
        assert captured["params"]["page"] == 2

    @pytest.mark.asyncio
    async def test_per_page_capped_at_80(self):
        captured: dict[str, Any] = {}

        async def capture_get(url: str, headers: Any = None, params: Any = None):
            captured["params"] = params
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"videos": []})
            return resp

        client = AsyncMock()
        client.get = AsyncMock(side_effect=capture_get)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        provider = PexelsVideoProvider()
        with patch("httpx.AsyncClient", return_value=ctx):
            await provider.fetch("q", config={"api_key": "k", "per_page": 500})

        assert captured["params"]["per_page"] == 80


class TestContract:
    def test_conforms_to_video_provider_protocol(self):
        from plugins.video_provider import VideoProvider
        provider = PexelsVideoProvider()
        assert isinstance(provider, VideoProvider)
        assert provider.name == "pexels_video"
        assert provider.kind == "search"

    def test_fetch_is_coroutine(self):
        import inspect
        assert inspect.iscoroutinefunction(PexelsVideoProvider.fetch)
