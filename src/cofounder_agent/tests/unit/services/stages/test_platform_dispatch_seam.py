"""Wave 3d-ii: platform handle dispatch seam tests.

Pins that when a ``platform`` is injected into the stage context, the
image-prompt helpers in ``source_featured_image`` and ``replace_inline_images``
call ``platform.dispatch.complete()`` instead of importing
``dispatch_complete`` directly from the services layer.

When ``platform`` is absent (substrate / legacy path), both functions fall
back to the direct substrate import — preserving backward-compat for callers
that don't wire a platform yet (e.g. test_replace_inline_images_task_id.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.fake_platform import FakePlatform


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _site_config(*, pool: Any = "sentinel") -> Any:
    """Stand-in SiteConfig.  ``pool`` defaults to a truthy sentinel so the
    ``getattr(site_config, '_pool', None)`` guard inside the helpers sees a
    live pool and proceeds to dispatch.  Pass ``pool=None`` to test the
    no-pool early-exit branch."""
    if pool == "sentinel":
        pool = MagicMock()
    sc = MagicMock()
    sc._pool = pool
    sc.get = MagicMock(side_effect=lambda k, d=None: d)
    sc.get_int = MagicMock(side_effect=lambda k, d=None: d)
    return sc


@asynccontextmanager
async def _noop_gpu_lock(*_args: Any, **_kwargs: Any):  # type: ignore[misc]
    """Drop-in for gpu.lock that never actually acquires anything."""
    yield


# ---------------------------------------------------------------------------
# source_featured_image._build_sdxl_prompt
# ---------------------------------------------------------------------------


class TestBuildSdxlPromptDispatch:
    """Pins the platform-vs-substrate dispatch routing in _build_sdxl_prompt."""

    _PROMPT_TEXT = "a neon-lit cyberpunk skyline at dusk, editorial magazine art"

    @pytest.mark.asyncio
    async def test_uses_platform_dispatch_when_set(self):
        from modules.content.stages.source_featured_image import _build_sdxl_prompt

        result_obj = MagicMock(text=self._PROMPT_TEXT)
        platform = FakePlatform(dispatch_response=result_obj)
        style_tracker = MagicMock(recent=MagicMock(return_value=[]))

        with patch(
            "modules.content.stages.source_featured_image._load_styles_from_settings",
            return_value=[("cyberpunk", "neon lights, dark city")],
        ), patch(
            "modules.content.stages.source_featured_image._load_recent_published_styles",
            AsyncMock(return_value=[]),
        ):
            text = await _build_sdxl_prompt(
                "AI and the future city",
                MagicMock(),
                style_tracker,
                site_config=_site_config(),
                platform=platform,
            )

        assert len(platform.dispatch.calls) == 1
        assert text == self._PROMPT_TEXT

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_import_when_no_platform(self):
        from modules.content.stages.source_featured_image import _build_sdxl_prompt

        result_obj = MagicMock(text=self._PROMPT_TEXT)
        dispatch_mock = AsyncMock(return_value=result_obj)
        style_tracker = MagicMock(recent=MagicMock(return_value=[]))

        with patch(
            "modules.content.stages.source_featured_image._load_styles_from_settings",
            return_value=[("nature", "mist, mountain, soft light")],
        ), patch(
            "modules.content.stages.source_featured_image._load_recent_published_styles",
            AsyncMock(return_value=[]),
        ), patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ):
            text = await _build_sdxl_prompt(
                "mountains at dawn",
                MagicMock(),
                style_tracker,
                site_config=_site_config(),
                platform=None,
            )

        assert dispatch_mock.await_count == 1
        assert text == self._PROMPT_TEXT

    @pytest.mark.asyncio
    async def test_no_pool_returns_fallback_without_dispatch(self):
        """No DB pool → deterministic style+tags fallback; neither path dispatches."""
        from modules.content.stages.source_featured_image import _build_sdxl_prompt

        platform = FakePlatform()
        style_tracker = MagicMock(recent=MagicMock(return_value=[]))

        with patch(
            "modules.content.stages.source_featured_image._load_styles_from_settings",
            return_value=[("minimal", "clean, geometric")],
        ), patch(
            "modules.content.stages.source_featured_image._load_recent_published_styles",
            AsyncMock(return_value=[]),
        ):
            text = await _build_sdxl_prompt(
                "topic",
                MagicMock(),
                style_tracker,
                site_config=_site_config(pool=None),
                platform=platform,
            )

        assert len(platform.dispatch.calls) == 0
        assert "minimal" in text


# ---------------------------------------------------------------------------
# replace_inline_images._try_sdxl
# ---------------------------------------------------------------------------


class TestTrySdxlDispatch:
    """Pins the platform-vs-substrate dispatch routing in _try_sdxl.

    ``_try_sdxl`` calls GPU-scheduler and the SDXL HTTP server after the
    prompt is built.  The tests end early by returning a sub-20-char
    dispatch response, which triggers the ``len(sdxl_prompt) <= 20``
    guard and returns ``None`` before any HTTP call is made.  That's
    enough to assert which dispatch path was taken.
    """

    @pytest.mark.asyncio
    async def test_uses_platform_dispatch_when_set(self):
        from modules.content.stages.replace_inline_images import _try_sdxl

        # Short prompt → function returns None after dispatch; no SDXL HTTP call.
        result_obj = MagicMock(text="short")
        platform = FakePlatform(dispatch_response=result_obj)
        gpu_mock = MagicMock(lock=_noop_gpu_lock)

        # gpu is a local import inside _try_sdxl — create=True is required.
        with patch(
            "modules.content.stages.replace_inline_images.gpu",
            gpu_mock,
            create=True,
        ):
            out = await _try_sdxl(
                "1",
                "the query",
                "the topic",
                site_config=_site_config(),
                task_id="task-test",
                platform=platform,
            )

        assert out is None  # short-prompt guard fired
        assert len(platform.dispatch.calls) == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_import_when_no_platform(self):
        from modules.content.stages.replace_inline_images import _try_sdxl

        result_obj = MagicMock(text="short")
        dispatch_mock = AsyncMock(return_value=result_obj)
        gpu_mock = MagicMock(lock=_noop_gpu_lock)

        with patch(
            "modules.content.stages.replace_inline_images.gpu",
            gpu_mock,
            create=True,
        ), patch(
            "services.llm_providers.dispatcher.dispatch_complete",
            dispatch_mock,
        ):
            out = await _try_sdxl(
                "2",
                "search terms",
                "article topic",
                site_config=_site_config(),
                task_id=None,
                platform=None,
            )

        assert out is None
        assert dispatch_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_no_pool_returns_none_without_dispatch(self):
        """No DB pool → returns None immediately; no dispatch on either path."""
        from modules.content.stages.replace_inline_images import _try_sdxl

        platform = FakePlatform()

        out = await _try_sdxl(
            "3",
            "query",
            "topic",
            site_config=_site_config(pool=None),
            task_id=None,
            platform=platform,
        )

        assert out is None
        assert len(platform.dispatch.calls) == 0
