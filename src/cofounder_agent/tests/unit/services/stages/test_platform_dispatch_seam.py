"""Wave 3d-ii / Wave 3f: platform handle dispatch seam tests.

Pins that when a ``platform`` is injected into the stage context, the
image-prompt helpers in ``source_featured_image`` and ``replace_inline_images``
call ``platform.dispatch.complete()`` instead of importing
``dispatch_complete`` directly from the services layer.

Wave 3f (#667): the legacy fallback path (direct substrate import when
``platform`` is None) is removed. Both functions now raise RuntimeError
if ``platform`` is absent — callers must always thread a platform handle.
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
# source_featured_image._build_image_gen_prompt
# ---------------------------------------------------------------------------


class TestBuildImageGenPromptDispatch:
    """Pins the platform-vs-substrate dispatch routing in _build_image_gen_prompt."""

    _PROMPT_TEXT = "a neon-lit cyberpunk skyline at dusk, editorial magazine art"

    @pytest.mark.asyncio
    async def test_uses_platform_dispatch_when_set(self):
        from modules.content.stages.source_featured_image import _build_image_gen_prompt

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
            text = await _build_image_gen_prompt(
                "AI and the future city",
                MagicMock(),
                style_tracker,
                site_config=_site_config(),
                platform=platform,
            )

        assert len(platform.dispatch.calls) == 1
        assert text == self._PROMPT_TEXT

    @pytest.mark.asyncio
    async def test_raises_when_no_platform(self):
        """Wave 3f (#667): platform=None raises RuntimeError (caught by caller's
        except-block), so _build_image_gen_prompt returns the deterministic fallback
        string instead of calling dispatch_complete."""
        from modules.content.stages.source_featured_image import _build_image_gen_prompt

        style_tracker = MagicMock(recent=MagicMock(return_value=[]))

        with patch(
            "modules.content.stages.source_featured_image._load_styles_from_settings",
            return_value=[("nature", "mist, mountain, soft light")],
        ), patch(
            "modules.content.stages.source_featured_image._load_recent_published_styles",
            AsyncMock(return_value=[]),
        ):
            text = await _build_image_gen_prompt(
                "mountains at dawn",
                MagicMock(),
                style_tracker,
                site_config=_site_config(),
                platform=None,
            )

        # RuntimeError is caught by the try/except and the deterministic fallback
        # style+tags string is returned — not the LLM-generated prompt.
        assert text is not None
        assert "nature" in text

    @pytest.mark.asyncio
    async def test_no_pool_returns_fallback_without_dispatch(self):
        """No DB pool → deterministic style+tags fallback; neither path dispatches."""
        from modules.content.stages.source_featured_image import _build_image_gen_prompt

        platform = FakePlatform()
        style_tracker = MagicMock(recent=MagicMock(return_value=[]))

        with patch(
            "modules.content.stages.source_featured_image._load_styles_from_settings",
            return_value=[("minimal", "clean, geometric")],
        ), patch(
            "modules.content.stages.source_featured_image._load_recent_published_styles",
            AsyncMock(return_value=[]),
        ):
            text = await _build_image_gen_prompt(
                "topic",
                MagicMock(),
                style_tracker,
                site_config=_site_config(pool=None),
                platform=platform,
            )

        assert len(platform.dispatch.calls) == 0
        assert "minimal" in text


# ---------------------------------------------------------------------------
# replace_inline_images._try_image_gen
# ---------------------------------------------------------------------------


class TestTryImageGenDispatch:
    """Pins the platform-vs-substrate dispatch routing in _try_image_gen.

    ``_try_image_gen`` calls GPU-scheduler and the image-gen HTTP server after the
    prompt is built.  The tests end early by returning a sub-20-char
    dispatch response, which triggers the ``len(sdxl_prompt) <= 20``
    guard and returns ``None`` before any HTTP call is made.  That's
    enough to assert which dispatch path was taken.
    """

    @pytest.mark.asyncio
    async def test_uses_platform_dispatch_when_set(self):
        from modules.content.stages.replace_inline_images import _try_image_gen

        # Short prompt → function returns None after dispatch; no image-gen HTTP call.
        result_obj = MagicMock(text="short")
        platform = FakePlatform(dispatch_response=result_obj)
        gpu_mock = MagicMock(lock=_noop_gpu_lock)

        # gpu is a local import inside _try_image_gen — create=True is required.
        with patch(
            "modules.content.stages.replace_inline_images.gpu",
            gpu_mock,
            create=True,
        ):
            out = await _try_image_gen(
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
    async def test_raises_when_no_platform(self):
        """Wave 3f (#667): platform=None raises RuntimeError; _try_image_gen catches
        it and returns None (non-critical path — image is skipped, not fatal)."""
        from modules.content.stages.replace_inline_images import _try_image_gen

        gpu_mock = MagicMock(lock=_noop_gpu_lock)

        with patch(
            "modules.content.stages.replace_inline_images.gpu",
            gpu_mock,
            create=True,
        ):
            out = await _try_image_gen(
                "2",
                "search terms",
                "article topic",
                site_config=_site_config(),
                task_id=None,
                platform=None,
            )

        # RuntimeError is caught inside _try_image_gen (non-critical inline image)
        # and the function returns None.
        assert out is None

    @pytest.mark.asyncio
    async def test_no_pool_returns_none_without_dispatch(self):
        """No DB pool → returns None immediately; no dispatch on either path."""
        from modules.content.stages.replace_inline_images import _try_image_gen

        platform = FakePlatform()

        out = await _try_image_gen(
            "3",
            "query",
            "topic",
            site_config=_site_config(pool=None),
            task_id=None,
            platform=platform,
        )

        assert out is None
        assert len(platform.dispatch.calls) == 0
