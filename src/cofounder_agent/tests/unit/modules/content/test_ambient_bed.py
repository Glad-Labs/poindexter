"""The shared music bed: one implementation for the blog stage and news Shorts.

``generate_ambient_bed`` renders and raises; ``ambient_bed_or_nothing`` is the
best-effort form a graph node uses. The GPU lock is replaced with a no-op so no
test reaches the real cross-process lock.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content import ambient_bed as ab
from poindexter.services.gpu_admission import GpuBusyError

_MOD = "poindexter.modules.content.ambient_bed"


@contextlib.asynccontextmanager
async def _fake_lock(*_a: Any, **_kw: Any):
    yield


@pytest.fixture(autouse=True)
def _neutralize_gpu_lock():
    with patch("poindexter.services.gpu_scheduler.gpu.lock", _fake_lock):
        yield


class _Cfg(dict):
    def get(self, key, default=None):  # noqa: D102 - dict-shaped SiteConfig stand-in
        return super().get(key, default)


def _kwargs(**over):
    base = dict(site_config=_Cfg(), output_dir="/data/video", output_stem="t1_ambient", task_id="t1")
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_the_bed_is_rendered_to_the_durable_path_at_full_length():
    gen = AsyncMock(return_value=SimpleNamespace(file_path="/data/video/t1_ambient.wav"))
    path = await ab.generate_ambient_bed(
        "a GPU shipment goes missing", generate=gen, is_enabled=lambda _sc: True, **_kwargs(),
    )
    assert path == "/data/video/t1_ambient.wav"
    args, kw = gen.await_args
    assert args[1] == "ambient"
    assert "a GPU shipment goes missing" in args[0]
    assert kw["output_dir"] == "/data/video" and kw["output_stem"] == "t1_ambient"
    assert kw["duration_s"] == ab.AMBIENT_BED_SECONDS


@pytest.mark.asyncio
async def test_the_operators_template_is_used_with_the_mood_filled_in():
    gen = AsyncMock(return_value=SimpleNamespace(file_path="/x.wav"))
    cfg = _Cfg(audio_gen_ambient_prompt_template="synthwave, {mood}")
    await ab.generate_ambient_bed(
        "rain", generate=gen, is_enabled=lambda _sc: True, **_kwargs(site_config=cfg),
    )
    assert gen.await_args.args[0] == "synthwave, rain"


@pytest.mark.asyncio
async def test_no_audio_engine_means_no_bed_and_no_render():
    gen = AsyncMock()
    path = await ab.generate_ambient_bed("x", generate=gen, is_enabled=lambda _sc: False, **_kwargs())
    assert path == "" and gen.await_count == 0


@pytest.mark.asyncio
async def test_the_raising_form_lets_a_busy_gpu_through():
    """The stage reports a busy GPU with its own handler, so this must raise."""
    gen = AsyncMock(side_effect=GpuBusyError("render holds the card", eta_seconds=60))
    with pytest.raises(GpuBusyError):
        await ab.generate_ambient_bed("x", generate=gen, is_enabled=lambda _sc: True, **_kwargs())


@pytest.mark.asyncio
async def test_best_effort_reports_a_busy_gpu_and_returns_nothing():
    with patch(f"{_MOD}.generate_ambient_bed",
               new=AsyncMock(side_effect=GpuBusyError("busy", eta_seconds=30))), \
         patch(f"{_MOD}.surface_media_gpu_busy_skip") as skip:
        path = await ab.ambient_bed_or_nothing("x", stage="media.generate_ambient_bed", **_kwargs())
    assert path == ""
    skip.assert_called_once()
    assert skip.call_args.args[0] == "media.generate_ambient_bed"


@pytest.mark.asyncio
async def test_best_effort_survives_a_failed_render():
    with patch(f"{_MOD}.generate_ambient_bed", new=AsyncMock(side_effect=RuntimeError("boom"))):
        path = await ab.ambient_bed_or_nothing("x", stage="s", **_kwargs())
    assert path == ""


def test_mood_cues_strip_markdown_and_skip_short_entries():
    scenes = ["### PART 1", "Scene 1: a **server room** glowing blue under a slow pan"]
    assert ab.ambient_mood_cues(scenes) == "a server room glowing blue under a slow pan"
    assert ab.ambient_mood_cues([]) == ""
