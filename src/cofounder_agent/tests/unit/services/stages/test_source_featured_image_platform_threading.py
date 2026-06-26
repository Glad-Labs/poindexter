"""Regression test: the featured-image stage must thread the ``platform``
capability handle into the image-gen prompt build.

Production bug (observed live 2026-06-18, canonical_blog graph_def path):
``modules.content.stages.source_featured_image`` logged on EVERY run

    [IMAGE] LLM prompt generation failed, using fallback:
    platform handle required for dispatch — check pipeline context threading

while the inline-image atom (``content.generate_images`` → ``_try_image_gen``)
produced specific, LLM-crafted prompts on the same runs. Root cause: the
``platform`` handle IS present in the stage context (threaded by the
TemplateRunner via ``RunnableConfig.configurable["__services__"]`` — the
writer + inline atom both receive it), but ``SourceFeaturedImageStage.execute``
never read ``context.get("platform")`` and called ``_try_image_gen_featured(...)``
without ``platform=``. The default ``platform=None`` then raised inside
``_build_image_gen_prompt`` and the stage fell back to the bare
``{style}, {style_tags}, no text, no faces`` prompt — generic, not
subject-specific.

This is the #667 Seam 1 Wave 3f ("platform handle for dispatch.complete")
pattern, mirroring how ``replace_inline_images`` already threads
``context.get("platform")`` into ``_try_image_gen`` / ``_batch_generate_all_images``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _make_context(platform: Any, site_config: Any) -> dict[str, Any]:
    image_service = MagicMock()
    image_service.search_featured_image = AsyncMock(return_value=None)
    return {
        "topic": "RTX 5090 memory bandwidth deep dive",
        "tags": [],
        "generate_featured_image": True,
        "task_id": "test-task-id",
        "post_id": None,
        "image_service": image_service,
        "site_config": site_config,
        # The capability handle threaded onto the stage context by the
        # TemplateRunner (__services__ merge). Present here exactly as the
        # writer + inline atom receive it on the graph_def path.
        "platform": platform,
        # Provide a tracker so execute() doesn't build a real one.
        "image_style_tracker": MagicMock(recent=MagicMock(return_value=[])),
        "stages": {},
    }


@pytest.mark.asyncio
async def test_execute_threads_context_platform_into_try_image_gen_featured() -> None:
    """``execute`` must forward ``context['platform']`` to ``_try_image_gen_featured``.

    Pre-fix the stage dropped the handle: ``_try_image_gen_featured`` was called
    without ``platform=``, so the image-gen prompt build received ``platform=None``
    and fell back to the deterministic style-only prompt on every run.
    """
    from modules.content.stages.source_featured_image import SourceFeaturedImageStage

    sentinel_platform = MagicMock(name="capability_handle")
    site_config = _make_site_config({
        "image_gen_enabled": "true",
        "image_gen_server_url": "http://fake-image-gen:9836",
    })
    context = _make_context(sentinel_platform, site_config)

    gen_mock = AsyncMock(return_value=None)
    with patch(
        "modules.content.stages.source_featured_image._try_image_gen_featured",
        gen_mock,
    ):
        stage = SourceFeaturedImageStage()
        await stage.execute(context, config={})

    assert gen_mock.await_count == 1, "image-gen featured path was not attempted"
    threaded = gen_mock.await_args.kwargs.get("platform")
    assert threaded is sentinel_platform, (
        "execute() did not thread context['platform'] into _try_image_gen_featured "
        f"(got {threaded!r}). Without it, _build_image_gen_prompt raises "
        "'platform handle required for dispatch' and the featured image falls "
        "back to the bare style-only prompt — the 2026-06-18 production bug."
    )


@pytest.mark.asyncio
async def test_build_image_gen_prompt_dispatches_to_llm_when_platform_present() -> None:
    """End-to-end of the now-reachable branch: with a platform handle + pool,
    ``_build_image_gen_prompt`` dispatches to the LLM and returns its crafted,
    subject-specific prompt — not the deterministic style-only fallback.

    This branch was effectively dead in production: ``platform`` never reached
    the featured stage, so ``platform.dispatch.complete`` here had never run.
    The threading fix makes it live; this pins that it produces an LLM prompt.
    """
    from modules.content.stages import source_featured_image as mod

    site_config = _make_site_config({})
    site_config._pool = MagicMock()  # non-None pool → reach the dispatch branch

    completion = MagicMock()
    completion.text = "An isometric data-center scene rendered in flat vector style"
    platform = MagicMock()
    platform.dispatch.complete = AsyncMock(return_value=completion)

    style_tracker = MagicMock(recent=MagicMock(return_value=[]))
    picked: list[str] = []

    prompt = await mod._build_image_gen_prompt(
        "RTX 5090 memory bandwidth",
        picked.append,  # on_style_picked
        style_tracker,
        site_config=site_config,
        platform=platform,
    )

    platform.dispatch.complete.assert_awaited_once()
    assert prompt == "An isometric data-center scene rendered in flat vector style"
    assert "no text, no faces" not in prompt, "fell back despite platform present"


@pytest.mark.asyncio
async def test_build_image_gen_prompt_falls_back_without_platform() -> None:
    """Symmetry guard: no platform handle → graceful deterministic fallback
    (the retained pre-fix behavior for the None case, e.g. tests/bootstrap)."""
    from modules.content.stages import source_featured_image as mod

    site_config = _make_site_config({})
    site_config._pool = MagicMock()
    style_tracker = MagicMock(recent=MagicMock(return_value=[]))

    prompt = await mod._build_image_gen_prompt(
        "RTX 5090 memory bandwidth",
        lambda _s: None,
        style_tracker,
        site_config=site_config,
        platform=None,
    )

    assert "no text, no faces" in prompt, (
        "expected the style-only fallback shape when no platform handle is present"
    )
