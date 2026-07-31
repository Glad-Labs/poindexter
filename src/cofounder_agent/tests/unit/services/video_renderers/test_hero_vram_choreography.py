"""Render-lane VRAM choreography — poindexter#907 defect 2.

The dispatch-time VRAM gate checks free VRAM ONCE, before the media flow
starts, and holds no reservation for the minutes the render takes. Measured
2026-07-29 on the operator box:

    05:03:45  gate passes — 29.4 GB free on GPU0
    05:03-07  image-gen loads 25.1 GB (illustrating the NEXT article)
    05:06:36  wan OOMs — 98 MB free, its own process holding just 1.82 GB
              -> hero_render_fallback (18 of them across the fleet)

wan was CROWDED OUT, not too big. No threshold value fixes that, because the
crowding happens after the check — so the hero path clears the card itself
immediately before loading wan.

Pinned here: the unload happens before the provider is invoked, it is
best-effort (never converts a possible render into a certain skip), and it is
operator-disableable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig


def _sc(**over):
    base = {"video_hero_unload_settle_seconds": "0"}
    base.update(over)
    return SiteConfig(initial_config=base)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_gen_is_hard_unloaded_before_the_hero_load():
    """A soft unload does not return the VRAM — the process keeps its CUDA
    reserved pool — so this must be the HARD unload or the fix does nothing."""
    from services.video_renderers import shot_list_renderer as slr

    unload = AsyncMock()
    with patch("services.gpu_scheduler.gpu._unload_image_gen", unload), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(_sc())

    unload.assert_awaited_once_with(hard=True)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unload_precedes_the_wan_provider_call():
    """Ordering is the whole fix: unloading after the load would be pointless."""
    from services.video_renderers import shot_list_renderer as slr

    order = []

    async def _fake_clear(_sc_arg):
        order.append("unload")

    class _Provider:
        async def fetch(self, *a, **kw):
            order.append("wan_fetch")
            return []

    with patch.object(slr, "_clear_image_gen_for_hero", _fake_clear), \
         patch("services.video_providers.wan2_1.Wan21Provider", _Provider):
        await slr._render_generative_clip(
            prompt="a glowing server rack", output_path="/tmp/x.mp4",
            image_path=None, duration_s=5, site_config=_sc(),
        )

    assert order == ["unload", "wan_fetch"], f"got {order}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unload_failure_still_attempts_the_render():
    """Best-effort by design. A reclaim that fails must not turn a POSSIBLE
    render into a CERTAIN skip — the pre-fix behaviour (wan may OOM and fall
    back to a still) is strictly better than not trying."""
    from services.video_renderers import shot_list_renderer as slr

    with patch(
        "services.gpu_scheduler.gpu._unload_image_gen",
        AsyncMock(side_effect=RuntimeError("image-gen unreachable")),
    ), patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(_sc())  # must not raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_operator_can_disable_the_unload():
    """A card that comfortably fits both should not pay image-gen's cold
    reload on every hero clip."""
    from services.video_renderers import shot_list_renderer as slr

    unload = AsyncMock()
    with patch("services.gpu_scheduler.gpu._unload_image_gen", unload):
        await slr._clear_image_gen_for_hero(
            _sc(video_hero_unload_image_gen="false"),
        )

    unload.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settings_read_failure_defaults_to_unloading():
    """Fail toward the safer behaviour: a broken settings read must not
    silently disable the protection."""
    from services.video_renderers import shot_list_renderer as slr

    broken = MagicMock()
    broken.get_bool.side_effect = RuntimeError("settings down")
    broken.get_float.return_value = 0.0
    unload = AsyncMock()
    with patch("services.gpu_scheduler.gpu._unload_image_gen", unload), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(broken)

    unload.assert_awaited_once_with(hard=True)
