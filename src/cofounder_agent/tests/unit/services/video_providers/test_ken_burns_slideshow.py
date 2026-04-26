"""Unit tests for ``services/video_providers/ken_burns_slideshow.py``.

GH#124 — wraps the legacy slideshow pipeline as a VideoProvider so
swapping engines is a single ``app_settings.video_engine`` flip rather
than a code change. Tests:

- Protocol conformance (kind="compose")
- Delegates to ``services.video_service.generate_video_for_post`` for
  long-form, ``generate_short_video_for_post`` for shorts
- Translates the legacy VideoResult into the plugin VideoResult shape
- Fails loud when ``post_id`` or ``_site_config`` are missing
- Returns ``[]`` when the legacy pipeline fails
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.video_provider import VideoProvider, VideoResult
from services.video_providers.ken_burns_slideshow import (
    KenBurnsSlideshowProvider,
)
from services.video_service import VideoResult as LegacyVideoResult


def _mock_sc() -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda k, d="": d
    return sc


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert KenBurnsSlideshowProvider.name == "ken_burns_slideshow"

    def test_kind_is_compose(self):
        # Slideshow is image+audio composition, not true T2V.
        assert KenBurnsSlideshowProvider.kind == "compose"

    def test_conforms_to_video_provider_protocol(self):
        assert isinstance(KenBurnsSlideshowProvider(), VideoProvider)


@pytest.mark.unit
@pytest.mark.asyncio
class TestFetch:
    async def test_empty_title_returns_empty(self):
        provider = KenBurnsSlideshowProvider()
        assert await provider.fetch("", {"post_id": "p1"}) == []

    async def test_missing_post_id_returns_empty(self):
        provider = KenBurnsSlideshowProvider()
        assert await provider.fetch("Title", {}) == []

    async def test_missing_site_config_returns_empty(self):
        provider = KenBurnsSlideshowProvider()
        # post_id present but no _site_config
        assert await provider.fetch("Title", {"post_id": "p1"}) == []

    async def test_long_form_delegates_to_generate_video_for_post(self):
        provider = KenBurnsSlideshowProvider()

        with patch(
            "services.video_service.generate_video_for_post",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = LegacyVideoResult(
                success=True,
                file_path="/tmp/v.mp4",
                duration_seconds=120,
                file_size_bytes=4096,
                images_used=8,
            )
            results = await provider.fetch(
                "My Post Title",
                {
                    "post_id": "p1",
                    "content": "body",
                    "_site_config": _mock_sc(),
                    "short": False,
                },
            )

        mock_gen.assert_awaited_once()
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, VideoResult)
        assert r.file_url == "file:///tmp/v.mp4"
        assert r.file_path == "/tmp/v.mp4"
        assert r.duration_s == 120
        assert r.source == "ken_burns_slideshow"
        # Slideshow native landscape dimensions
        assert r.width == 1920
        assert r.height == 1080
        assert r.fps == 30
        assert r.codec == "h264"
        assert r.format == "mp4"
        assert r.metadata["images_used"] == 8
        assert r.metadata["file_size_bytes"] == 4096
        assert r.metadata["post_id"] == "p1"
        assert r.metadata["is_short"] is False

    async def test_short_dispatches_to_short_video(self):
        provider = KenBurnsSlideshowProvider()

        with patch(
            "services.video_service.generate_short_video_for_post",
            new_callable=AsyncMock,
        ) as mock_short:
            mock_short.return_value = LegacyVideoResult(
                success=True,
                file_path="/tmp/short.mp4",
                duration_seconds=60,
                file_size_bytes=2048,
                images_used=4,
            )
            results = await provider.fetch(
                "Title",
                {
                    "post_id": "p1",
                    "content": "body",
                    "_site_config": _mock_sc(),
                    "short": True,
                },
            )

        mock_short.assert_awaited_once()
        assert len(results) == 1
        r = results[0]
        assert r.file_path == "/tmp/short.mp4"
        # Vertical Shorts dimensions
        assert r.width == 1080
        assert r.height == 1920
        assert r.metadata["is_short"] is True

    async def test_legacy_failure_returns_empty(self):
        provider = KenBurnsSlideshowProvider()

        with patch(
            "services.video_service.generate_video_for_post",
            new_callable=AsyncMock,
        ) as mock_gen:
            mock_gen.return_value = LegacyVideoResult(
                success=False, error="no images available",
            )
            results = await provider.fetch(
                "Title",
                {
                    "post_id": "p1",
                    "content": "body",
                    "_site_config": _mock_sc(),
                },
            )

        assert results == []
