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


# ---------------------------------------------------------------------------
# Second co-resident: Ollama (poindexter#992, 2026-08-07)
#
# Clearing image-gen alone was not enough on the operator box. The media
# pipeline runs its OWN LLM work on this GPU — vision QA, caption re-scoring,
# self-consistency — and Ollama keeps the ~21 GB writer resident on its
# keep_alive timer long after the last call. Measured during take 10:
#
#     wan needs ~25.3 GB at 832x480
#     ollama still holds  21.4 GB
#     -> CUDA OOM "Tried to allocate 320.00 MiB" — missed by a rounding error
#
# So the pre-hero clear evicts Ollama too. Reload costs ~20-30s on the next
# QA call, against a 147s hero that would otherwise fail outright.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ollama_is_evicted_before_the_hero_load():
    from services.video_renderers import shot_list_renderer as slr

    unload_img = AsyncMock()
    unload_ollama = AsyncMock()
    with patch("services.gpu_scheduler.gpu._unload_image_gen", unload_img), \
         patch("services.gpu_scheduler.gpu._unload_ollama_models", unload_ollama), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(_sc())

    unload_ollama.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ollama_evict_is_operator_disableable():
    """An operator whose card fits both should not pay the writer's reload."""
    from services.video_renderers import shot_list_renderer as slr

    unload_img = AsyncMock()
    unload_ollama = AsyncMock()
    with patch("services.gpu_scheduler.gpu._unload_image_gen", unload_img), \
         patch("services.gpu_scheduler.gpu._unload_ollama_models", unload_ollama), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(_sc(video_hero_evict_ollama="false"))

    unload_ollama.assert_not_awaited()
    unload_img.assert_awaited_once_with(hard=True)  # image-gen still cleared


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ollama_evict_failure_still_attempts_the_render():
    """Same best-effort contract as the image-gen lever: a failed reclaim must
    never convert a possible render into a certain skip."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_scheduler.gpu._unload_image_gen", AsyncMock()), \
         patch(
             "services.gpu_scheduler.gpu._unload_ollama_models",
             AsyncMock(side_effect=RuntimeError("ollama unreachable")),
         ), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        await slr._clear_image_gen_for_hero(_sc())  # must not raise


# ---------------------------------------------------------------------------
# The hero fix's cost to the NEXT render (poindexter#992, 2026-08-07)
#
# _clear_image_gen_for_hero EXITS image-gen to free the card for wan. The next
# render's still phase then races a container cold-booting a 6B checkpoint
# (~45-60s): /generate answers 503, the renderer scores the shot un-renderable,
# and the backfill substitutes recycled art. Measured live: 7 of 11 shots
# substituted — the whole video degrades, not just the heroes.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_gen_ready_wait_returns_true_on_health_200():
    from services.video_renderers import shot_list_renderer as slr

    class _Resp:
        status_code = 200

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return _Resp()

    with patch("httpx.AsyncClient", lambda **kw: _Client()):
        assert await slr._wait_image_gen_ready(
            "http://image-gen:9836", None, budget_s=5,
        ) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_gen_ready_wait_times_out_without_raising():
    """Timeout must return False and let the render proceed — the shots then
    fall into the same substitute ladder as before, never worse."""
    from services.video_renderers import shot_list_renderer as slr

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise ConnectionError("cold booting")

    with patch("httpx.AsyncClient", lambda **kw: _Client()), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        assert await slr._wait_image_gen_ready(
            "http://image-gen:9836", None, budget_s=0,
        ) is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_gen_ready_wait_skips_when_url_missing():
    """No URL configured must not hang or raise."""
    from services.video_renderers import shot_list_renderer as slr

    assert await slr._wait_image_gen_ready("", None, budget_s=5) is False


# ---------------------------------------------------------------------------
# Adaptive hero plate (2026-08-08)
#
# The dispatcher pre-gates on media_render_min_free_vram_gb, but the DESKTOP
# keeps allocating after that check: Chrome + Electron apps + the COSMIC shell
# held ~3GB on GPU0 while wan wanted 26.7GB of a 32.6GB card, and the hero
# OOM'd on its last 320 MiB. The same code went 4-for-4 hours earlier on a
# quieter desktop — the operator should not have to close their browser.
# ---------------------------------------------------------------------------


def _registry(free_gb):
    reg = MagicMock()
    reg.free_gb = AsyncMock(return_value=free_gb)
    return reg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plate_steps_down_to_the_quality_floor():
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(23.0)):
        w, h = await slr._fit_hero_dims_to_free_vram(832, 480, _sc())

    assert (w, h) == (704, 400)  # the floor rung, not below it


@pytest.mark.unit
@pytest.mark.asyncio
async def test_below_the_floor_declines_to_animate():
    """The 2026-08-08 ladder went to 512x320 on the theory that a small clip
    upscaled still reads as motion. It does not — every hero rendered there
    came back as neon morphing garbage. Below the floor we ship the still.
    """
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(16.0)):
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) is None


@pytest.mark.unit
def test_no_ladder_rung_below_the_quality_floor():
    """Guard the floor itself: a future 'just one more rung' re-creates the
    slop. 704x400 is the smallest plate that renders coherently."""
    from services.video_renderers import shot_list_renderer as slr

    assert min(w for w, _h, _g in slr._HERO_PLATE_LADDER) >= 704
    assert min(h for _w, h, _g in slr._HERO_PLATE_LADDER) >= 400


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plate_is_unchanged_when_vram_is_ample():
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(30.0)):
        w, h = await slr._fit_hero_dims_to_free_vram(832, 480, _sc())

    assert (w, h) == (832, 480)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_portrait_orientation_is_preserved():
    """The 9:16 lane must stay vertical after a step-down."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(23.0)):
        w, h = await slr._fit_hero_dims_to_free_vram(480, 832, _sc())

    assert (w, h) == (400, 704)
    assert h > w


@pytest.mark.unit
@pytest.mark.asyncio
async def test_never_steps_up_past_operator_config():
    """An operator who pinned a small plate keeps it even on an empty card."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(31.0)):
        out = await slr._fit_hero_dims_to_free_vram(512, 320, _sc())

    assert out == (512, 320)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unreadable_probe_keeps_requested_dims():
    """An unreadable probe must never shrink a render that would have worked."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(None)):
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) == (832, 480)

    def _boom(**kw):
        raise RuntimeError("prometheus down")

    with patch("services.gpu_registry.GPURegistry", _boom):
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) == (832, 480)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_operator_can_disable_adaptation():
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(5.0)):
        out = await slr._fit_hero_dims_to_free_vram(
            832, 480, _sc(video_hero_adaptive_plate_enabled="false"),
        )

    assert out == (832, 480)  # opted out: no step-down AND no skip


# ---------------------------------------------------------------------------
# wan's own resident pool counts as available TO WAN (2026-08-09)
#
# The plate gate ran with wan already loaded from the previous hero, so raw
# free VRAM read ~1GB and it concluded there was no room for the model that
# was already resident — skipping every hero after the first. Logged "only
# 1.0GB free (quality floor needs 22GB)" while nvidia-smi showed 19GB free
# and wan held 23GB. The gate must ask "free + what wan already holds".
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wan_resident_pool_is_counted_as_available():
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(1.0)), \
         patch.object(slr, "_wan_resident_gb", AsyncMock(return_value=26.0)):
        # 1GB raw free + 26GB wan already holds = 27GB → full plate
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) == (832, 480)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unreadable_wan_health_falls_back_to_raw_free():
    """Under-counting only makes the check more conservative — never wrong in
    the direction that ships slop."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(30.0)), \
         patch.object(slr, "_wan_resident_gb", AsyncMock(return_value=0.0)):
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) == (832, 480)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_genuinely_full_card_still_declines():
    """The skip must survive the refinement: a card full of OTHER tenants
    (wan not loaded) still declines rather than rendering sub-floor."""
    from services.video_renderers import shot_list_renderer as slr

    with patch("services.gpu_registry.GPURegistry", lambda **kw: _registry(8.0)), \
         patch.object(slr, "_wan_resident_gb", AsyncMock(return_value=0.0)):
        assert await slr._fit_hero_dims_to_free_vram(832, 480, _sc()) is None


# ---------------------------------------------------------------------------
# Probe ordering + Prometheus scrape lag (2026-08-09)
#
# Two compounding mistakes, both mine:
#   1. The plate probe ran BEFORE the co-resident reclaim (which lives inside
#      _render_generative_clip), so it measured the card with image-gen still
#      holding ~25GB and skipped every hero: "only 3.1GB free" while the
#      reclaim was about to free 25GB.
#   2. The worker is GPU-less, so free VRAM comes from Prometheus on a ~10s
#      scrape. A single sample taken 3s after the reclaim still reports the
#      pre-reclaim figure.
# Net effect: the quality floor silently disabled the feature it protects.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_first_sample_does_not_decide():
    """A low sample followed by the post-reclaim truth must use the truth."""
    from services.video_renderers import shot_list_renderer as slr

    reg = MagicMock()
    reg.free_gb = AsyncMock(side_effect=[3.1, 3.1, 28.0])  # scrape catches up
    with patch("services.gpu_registry.GPURegistry", lambda **kw: reg), \
         patch.object(slr, "_wan_resident_gb", AsyncMock(return_value=0.0)), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        out = await slr._fit_hero_dims_to_free_vram(832, 480, _sc())

    assert out == (832, 480)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ample_first_sample_short_circuits():
    """No reason to wait out a scrape interval when the card is already free."""
    from services.video_renderers import shot_list_renderer as slr

    reg = MagicMock()
    reg.free_gb = AsyncMock(return_value=30.0)
    with patch("services.gpu_registry.GPURegistry", lambda **kw: reg), \
         patch.object(slr, "_wan_resident_gb", AsyncMock(return_value=0.0)), \
         patch.object(slr.asyncio, "sleep", AsyncMock()):
        out = await slr._fit_hero_dims_to_free_vram(832, 480, _sc())

    assert out == (832, 480)
    assert reg.free_gb.await_count == 1


@pytest.mark.unit
def test_reclaim_precedes_the_plate_probe_in_animate_hero():
    """Guard the ORDER — the whole defect was probing before reclaiming."""
    import inspect

    from services.video_renderers import shot_list_renderer as slr

    src = inspect.getsource(slr._animate_hero)
    assert src.index("_clear_image_gen_for_hero") < src.index(
        "_fit_hero_dims_to_free_vram",
    )
