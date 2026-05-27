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


def _fake_site_config(pool: Any | None = None, *, sdxl_enabled: bool = True):
    """SiteConfig stub. ``sdxl_enabled=False`` disables the SDXL HTTP path
    via the ``app_settings.sdxl_enabled`` gate added in #603 — tests that
    want to exercise the Pexels fallback must pass False (otherwise the
    SDXL HTTP server, if reachable in the dev env, will produce an
    image and the test asserts the wrong source URL)."""
    overrides = {"sdxl_enabled": "true" if sdxl_enabled else "false"}
    return SimpleNamespace(
        get=lambda k, d="": overrides.get(k, d if d is not None else ""),
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
        # sdxl_enabled=False so the new app_settings-driven SDXL gate
        # (#603) skips the HTTP path. Without this the test runs against
        # the dev SDXL server (if present) and the assertions about
        # pexels-shaped URLs fail.
        sc = _fake_site_config(pool=pool, sdxl_enabled=False)
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
        # SDXL is now always attempted (2026-05-27 gate change in source_featured_image.py);
        # force the SDXL path to miss so the Pexels fallback runs.
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=None),
        ), patch(
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
        # sdxl_enabled=False — exercises the no-image-found path without
        # contention from the new app_settings-driven SDXL gate (#603).
        sc = _fake_site_config(pool=MagicMock(), sdxl_enabled=False)
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
        # SDXL is now always attempted (2026-05-27 gate change in source_featured_image.py);
        # force the SDXL path to miss so search_featured_image is reached.
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=None),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            await SourceFeaturedImageStage().execute(ctx, {})
        recorder.assert_not_awaited()


# ---------------------------------------------------------------------------
# source_featured_image — featured_image_data context updates
# (closes the 2026-05-19 jank-audit dead-seam finding for the column)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFeaturedImageDataContextUpdates:
    """``source_featured_image`` populates context["featured_image_data"] so the
    publisher can persist it on ``posts.featured_image_data``.

    Pre-2026-05-19 the column was a silent dead seam — never written by
    any production code. These tests pin the contract for both SDXL and
    Pexels success branches.
    """

    async def test_sdxl_branch_populates_featured_image_data(self):
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        ctx: dict[str, Any] = {
            "task_id": "task-id-1",
            "post_id": "post-uuid-1",
            "topic": "Designing autonomous agents",
            "tags": [],
            "generate_featured_image": True,
            "image_service": SimpleNamespace(
                sdxl_available=True, sdxl_initialized=True,
                search_featured_image=AsyncMock(),
            ),
            "site_config": sc,
        }
        sdxl_img = GeneratedImage(
            url="https://r2.example/img.png",
            photographer="AI Generated (SDXL)",
            source="sdxl_local",
            sdxl_meta={
                "model": "sdxl_lightning",
                "seed": 42,
                "prompt": "editorial illustration of agents",
                "negative_prompt": "text, faces, hands",
                "generation_time_ms": 1850,
                "width": 1024,
                "height": 1024,
                "filename": "sdxl_abc.png",
            },
        )
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=sdxl_img),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            AsyncMock(return_value="asset-row"),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        assert result.ok is True
        fid = result.context_updates["featured_image_data"]
        assert fid["source"] == "sdxl_local"
        assert fid["provider_plugin"] == "image.sdxl_local"
        assert fid["sdxl_model"] == "sdxl_lightning"
        assert fid["sdxl_seed"] == 42
        assert fid["sdxl_prompt"] == "editorial illustration of agents"
        assert fid["sdxl_negative_prompt"] == "text, faces, hands"
        assert fid["sdxl_dimensions"] == [1024, 1024]
        # generation_time_ms = 1850 → 1.85s, rounded to 3 decimals
        assert fid["generation_seconds"] == 1.85
        assert fid["width"] == 1024
        assert fid["height"] == 1024
        assert fid["topic"] == "Designing autonomous agents"
        assert fid["photographer"] == "AI Generated (SDXL)"
        # generated_at must be ISO-8601 — exact value isn't pinned (it's
        # ``datetime.now``-derived), but the field must exist.
        assert "generated_at" in fid

    async def test_sdxl_branch_extends_media_assets_metadata(self):
        """The media_assets row gets the same SDXL params for one-stop debugging."""
        pool = MagicMock()
        sc = _fake_site_config(pool=pool)
        ctx: dict[str, Any] = {
            "task_id": "task-id-1",
            "post_id": "post-uuid-1",
            "topic": "AI",
            "tags": [],
            "generate_featured_image": True,
            "image_service": SimpleNamespace(
                sdxl_available=True, sdxl_initialized=True,
                search_featured_image=AsyncMock(),
            ),
            "site_config": sc,
        }
        sdxl_img = GeneratedImage(
            url="https://r2.example/img.png",
            photographer="AI Generated (SDXL)",
            source="sdxl_local",
            sdxl_meta={
                "model": "sdxl_lightning",
                "seed": 7,
                "prompt": "p",
                "negative_prompt": "n",
                "generation_time_ms": 100,
            },
        )
        recorder = AsyncMock(return_value="row")
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=sdxl_img),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            recorder,
        ):
            await SourceFeaturedImageStage().execute(ctx, {})

        recorder.assert_awaited_once()
        meta = recorder.await_args.kwargs["metadata"]
        assert meta["sdxl_model"] == "sdxl_lightning"
        assert meta["sdxl_seed"] == 7
        assert meta["sdxl_prompt"] == "p"
        assert meta["sdxl_negative_prompt"] == "n"
        assert meta["sdxl_generation_time_ms"] == 100

    async def test_pexels_branch_populates_featured_image_data(self):
        # sdxl_enabled=False — exercises the pexels-only branch under
        # the new app_settings-driven SDXL gate (#603).
        sc = _fake_site_config(pool=MagicMock(), sdxl_enabled=False)
        pexels_img = SimpleNamespace(
            url="https://pex.example/p.jpg",
            photographer="Alex",
            source="pexels",
            width=800, height=600,
        )
        image_service = SimpleNamespace(
            sdxl_available=False, sdxl_initialized=True,
            search_featured_image=AsyncMock(return_value=pexels_img),
        )
        ctx: dict[str, Any] = {
            "task_id": "t",
            "post_id": "post-uuid-2",
            "topic": "Cats",
            "tags": ["kittens"],
            "generate_featured_image": True,
            "image_service": image_service,
            "site_config": sc,
        }
        # SDXL is now always attempted (2026-05-27 gate change in source_featured_image.py);
        # force the SDXL path to miss so the Pexels branch populates featured_image_data.
        with patch(
            "services.stages.source_featured_image._try_sdxl_featured",
            AsyncMock(return_value=None),
        ), patch(
            "services.media_asset_recorder.record_media_asset",
            AsyncMock(return_value="row"),
        ):
            result = await SourceFeaturedImageStage().execute(ctx, {})

        fid = result.context_updates["featured_image_data"]
        assert fid["source"] == "pexels"
        assert fid["provider_plugin"] == "image.pexels"
        assert fid["width"] == 800
        assert fid["height"] == 600
        assert fid["photographer"] == "Alex"
        assert fid["topic"] == "Cats"
        # SDXL-only keys must NOT appear on the Pexels branch — keeps
        # operator queries like ``WHERE sdxl_model IS NOT NULL`` clean.
        assert "sdxl_model" not in fid
        assert "sdxl_seed" not in fid
        assert "sdxl_prompt" not in fid

    async def test_disabled_branch_omits_featured_image_data(self):
        """When generation is disabled, no featured_image_data is set.

        The publisher then writes ``{}`` (the default), which is the
        right value for the no-image branch.
        """
        sc = _fake_site_config(pool=MagicMock())
        ctx: dict[str, Any] = {
            "task_id": "t",
            "post_id": "post-uuid",
            "topic": "X",
            "tags": [],
            "generate_featured_image": False,
            "image_service": SimpleNamespace(
                sdxl_available=False, sdxl_initialized=True,
            ),
            "site_config": sc,
        }
        result = await SourceFeaturedImageStage().execute(ctx, {})
        assert "featured_image_data" not in result.context_updates


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
