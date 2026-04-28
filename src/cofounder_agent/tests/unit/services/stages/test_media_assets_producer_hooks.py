"""Tests for the ``media_assets`` producer hooks added by GH#161.

Pre-fix, ``source_featured_image``, ``replace_inline_images``,
``PodcastService.generate_episode``, and the legacy
``video_service.generate_video_for_post`` all produced files but
skipped the ``media_assets`` INSERT. This module asserts that the
hooks now fire for every successful production path.

The recorder itself is mocked — these tests verify the *call shape*
the producers send into the recorder, not the SQL the recorder
emits (that's covered by ``test_media_asset_recorder.py``).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.stages.replace_inline_images import ReplaceInlineImagesStage
from services.stages.source_featured_image import (
    GeneratedImage,
    SourceFeaturedImageStage,
)


def _fake_site_config(pool: Any | None = None):
    return SimpleNamespace(
        get=lambda k, d="": d if d is not None else "",
        get_int=lambda _k, d=0: d,
        get_float=lambda _k, d=0.0: d,
        get_bool=lambda _k, d=False: d,
        _pool=pool,
    )


# ---------------------------------------------------------------------------
# source_featured_image — SDXL + Pexels both record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSourceFeaturedImageRecordsAsset:
    async def test_sdxl_success_records_media_asset(self):
        pool = MagicMock()  # marker — recorder is patched, value doesn't matter
        sc = _fake_site_config(pool=pool)
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "post_id": "post-uuid-1",
            "topic": "AI agents",
            "tags": [],
            "generate_featured_image": True,
            "image_service": SimpleNamespace(
                sdxl_available=True, sdxl_initialized=True,
                search_featured_image=AsyncMock(),
            ),
            "site_config": sc,
        }
        recorder = AsyncMock(return_value="asset-row-1")
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=GeneratedImage(
                url="https://r2.example/featured.png",
                photographer="AI Generated (SDXL)",
                source="sdxl_local",
            )),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        assert result.ok is True
        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "featured_image"
        assert kwargs["post_id"] == "post-uuid-1"
        assert kwargs["public_url"] == "https://r2.example/featured.png"
        assert kwargs["provider_plugin"] == "image.sdxl_local"
        assert kwargs["pool"] is pool

    async def test_pexels_success_records_media_asset(self):
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        pexels_img = SimpleNamespace(
            url="https://pex.example/photo.jpg",
            photographer="Alex",
            source="pexels",
            width=800, height=600,
        )
        image_service = SimpleNamespace(
            sdxl_available=False, sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=pexels_img),
        )
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "post_id": "post-uuid-2",
            "topic": "Cats",
            "tags": ["kittens"],
            "generate_featured_image": True,
            "image_service": image_service,
            "site_config": sc,
        }
        recorder = AsyncMock(return_value="asset-row-2")
        with patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        assert result.ok is True
        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "featured_image"
        assert kwargs["post_id"] == "post-uuid-2"
        assert kwargs["public_url"] == "https://pex.example/photo.jpg"
        assert kwargs["provider_plugin"] == "image.pexels"

    async def test_no_image_found_does_not_record(self):
        sc = _fake_site_config(pool=MagicMock())
        image_service = SimpleNamespace(
            sdxl_available=False, sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=None),
        )
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "post_id": "post-uuid-3",
            "topic": "X",
            "tags": [],
            "generate_featured_image": True,
            "image_service": image_service,
            "site_config": sc,
        }
        recorder = AsyncMock()
        with patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            await SourceFeaturedImageStage().execute(ctx, {})
        recorder.assert_not_awaited()


# ---------------------------------------------------------------------------
# replace_inline_images — SDXL + Pexels both record per placeholder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestReplaceInlineImagesRecordsAsset:
    async def test_sdxl_inline_success_records(self):
        sc = _fake_site_config(pool=MagicMock())
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "post_id": "post-uuid-1",
            "topic": "AI",
            "content": "Body before.\n\n[IMAGE-1]\n\nBody after.",
            "category": "technology",
            "site_config": sc,
            "image_service": SimpleNamespace(
                search_featured_image=AsyncMock(return_value=None),
            ),
            "database_service": SimpleNamespace(
                update_task=AsyncMock(),
            ),
        }
        recorder = AsyncMock(return_value="asset-uuid")
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value="https://r2.example/inline-1.png"),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            result = await ReplaceInlineImagesStage().execute(ctx, {})

        assert result.ok is True
        # One placeholder → one recorder call
        assert recorder.await_count == 1
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "inline_image"
        assert kwargs["post_id"] == "post-uuid-1"
        assert kwargs["public_url"] == "https://r2.example/inline-1.png"
        assert kwargs["provider_plugin"] == "image.sdxl"

    async def test_pexels_inline_success_records(self):
        sc = _fake_site_config(pool=MagicMock())
        pexels_img = SimpleNamespace(
            url="https://pex.example/p.jpg", photographer="Sam",
        )
        ctx: dict[str, Any] = {
            "task_id": "t1",
            "post_id": "post-uuid-2",
            "topic": "AI",
            "content": "Body.\n\n[IMAGE-1]\n\nMore body.",
            "category": "technology",
            "site_config": sc,
            "image_service": SimpleNamespace(
                search_featured_image=AsyncMock(return_value=pexels_img),
            ),
            "database_service": SimpleNamespace(
                update_task=AsyncMock(),
            ),
        }
        recorder = AsyncMock(return_value="asset-uuid")
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value=None),  # SDXL fails → falls through to Pexels
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            await ReplaceInlineImagesStage().execute(ctx, {})

        assert recorder.await_count == 1
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "inline_image"
        assert kwargs["public_url"] == "https://pex.example/p.jpg"
        assert kwargs["provider_plugin"] == "image.pexels"

    async def test_no_post_id_skips_recording(self):
        # Early-pipeline runs before post_id is known should not record.
        sc = _fake_site_config(pool=MagicMock())
        ctx: dict[str, Any] = {
            "task_id": "t1",
            # no post_id
            "topic": "AI",
            "content": "Body.\n\n[IMAGE-1]\n\nMore body.",
            "category": "technology",
            "site_config": sc,
            "image_service": SimpleNamespace(
                search_featured_image=AsyncMock(return_value=None),
            ),
            "database_service": SimpleNamespace(
                update_task=AsyncMock(),
            ),
        }
        recorder = AsyncMock()
        with patch(
            "services.stages.replace_inline_images._try_sdxl",
            AsyncMock(return_value="https://r2.example/x.png"),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            await ReplaceInlineImagesStage().execute(ctx, {})

        recorder.assert_not_awaited()
