"""Regression test for the 2026-05-27 image-gen-attempt-gate fix.

Pins the production observation: 9 of the last 12 published posts'
featured images came from R2/image-gen pre-2026-05-25 and pexels-only
post-2026-05-25. Root cause was the worker container losing the
``ml`` extras (diffusers + torch) so ``image_service.gen_available``
became permanently False. The pre-fix gate
``image_gen_attempted = gen_available or not gen_initialized`` then
skipped the HTTP image-gen path on every run.

The fix: ``image_gen_attempted`` is now ``app_settings.image_gen_enabled``
(default True). The HTTP path's natural ``return None`` on transport
failure handles image-gen-server-down gracefully — the gate no longer
gates on local-diffusers state that's not relevant to the HTTP path.

A regression that reintroduces the local-diffusers check would put
every featured image back on pexels, which Matt called out as a
content-quality issue (he wants a mix of image-gen + pexels, not pexels-only).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_image_service(gen_available: bool, gen_initialized: bool) -> Any:
    """Stand-in for ImageService with controllable image-gen flags + the
    Pexels search method the stage falls through to."""
    svc = MagicMock()
    svc.gen_available = gen_available
    svc.gen_initialized = gen_initialized
    svc.search_featured_image = AsyncMock(return_value=None)
    return svc


def _make_site_config(settings: dict[str, Any]) -> Any:
    sc = MagicMock()
    sc.get = MagicMock(side_effect=lambda k, default=None: settings.get(k, default))

    def _get_int(k, default=0):
        try:
            return int(settings.get(k, default))
        except (TypeError, ValueError):
            return default

    def _get_float(k, default=0.0):
        try:
            return float(settings.get(k, default))
        except (TypeError, ValueError):
            return default

    sc.get_int = MagicMock(side_effect=_get_int)
    sc.get_float = MagicMock(side_effect=_get_float)
    return sc


def _make_context(image_service: Any, site_config: Any | None = None) -> dict[str, Any]:
    return {
        "topic": "Test article",
        "tags": [],
        "generate_featured_image": True,
        "task_id": "test-task-id",
        "post_id": None,
        "image_service": image_service,
        "site_config": site_config,
        "stages": {},
    }


@pytest.mark.asyncio
async def test_image_gen_attempted_when_local_diffusers_missing() -> None:
    """The 2026-05-27 regression: worker container lacks diffusers
    so ``gen_available=False`` and ``gen_initialized=True`` after
    the first lazy init attempt. Pre-fix the gate evaluated to
    ``False or not True == False`` and image-gen was skipped on every run.

    Verify the new gate calls ``_try_image_gen_featured`` regardless of
    local-diffusers state."""
    from modules.content.stages.source_featured_image import SourceFeaturedImageStage

    image_service = _make_image_service(
        gen_available=False, gen_initialized=True,
    )
    site_config = _make_site_config({
        "image_gen_enabled": "true",
        "image_gen_server_url": "http://fake-image-gen:9836",
    })
    context = _make_context(image_service, site_config)

    gen_mock = AsyncMock(return_value=None)  # Skip the image-gen call's side-effects
    with patch(
        "modules.content.stages.source_featured_image._try_image_gen_featured",
        gen_mock,
    ):
        stage = SourceFeaturedImageStage()
        await stage.execute(context, config={})

    assert gen_mock.await_count == 1, (
        f"image-gen was not attempted despite local-diffusers being unavailable. "
        f"call_count={gen_mock.await_count}. Pre-fix this was 0 — the gate "
        "skipped image-gen based on the local-diffusers flag, which is irrelevant "
        "to the HTTP-server path."
    )


@pytest.mark.asyncio
async def test_image_gen_skipped_when_explicitly_disabled() -> None:
    """The new gate honours ``app_settings.image_gen_enabled=false`` so
    operators can disable image-gen during maintenance windows without
    code edits."""
    from modules.content.stages.source_featured_image import SourceFeaturedImageStage

    image_service = _make_image_service(gen_available=True, gen_initialized=True)
    site_config = _make_site_config({
        "image_gen_enabled": "false",
        "image_gen_server_url": "http://fake-image-gen:9836",
    })
    context = _make_context(image_service, site_config)

    gen_mock = AsyncMock(return_value=None)
    with patch(
        "modules.content.stages.source_featured_image._try_image_gen_featured",
        gen_mock,
    ):
        stage = SourceFeaturedImageStage()
        await stage.execute(context, config={})

    assert gen_mock.await_count == 0, (
        "image-gen attempted despite image_gen_enabled=false — the operator opt-out "
        "knob is broken."
    )


@pytest.mark.asyncio
async def test_image_gen_attempted_by_default_when_setting_unset() -> None:
    """No ``image_gen_enabled`` setting in app_settings — default to attempting
    image-gen. Fresh installs shouldn't have to set the flag to get image-gen working."""
    from modules.content.stages.source_featured_image import SourceFeaturedImageStage

    image_service = _make_image_service(gen_available=False, gen_initialized=True)
    site_config = _make_site_config({})  # no image_gen_enabled key
    context = _make_context(image_service, site_config)

    gen_mock = AsyncMock(return_value=None)
    with patch(
        "modules.content.stages.source_featured_image._try_image_gen_featured",
        gen_mock,
    ):
        stage = SourceFeaturedImageStage()
        await stage.execute(context, config={})

    assert gen_mock.await_count == 1
