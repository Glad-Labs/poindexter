"""render_shot_list emits per-shot progress through an optional progress_cb so
the media pulse row shows 'shot i/N' with honest position-pct. The callback is
best-effort — a raising callback must never break the render. Progress is
emitted in _render_pass (where the per-shot loop lives); render_shot_list just
forwards the callback."""
from unittest.mock import AsyncMock, patch

import pytest

from schemas.video_shot_list import Shot, VideoShotList
from services.video_renderers import shot_list_renderer as slr

pytestmark = pytest.mark.asyncio


def _shots3():
    """Three Shot objects — bypasses the VideoShotList cross-shot 'no >2
    consecutive same source' rule (not what these tests exercise;
    _render_one_shot is mocked anyway)."""
    return [
        Shot.model_validate(
            {
                "idx": i,
                "duration_s": 3.0,
                "intent": "x",
                "source": "image_gen",
                "prompt": f"p{i}",
                "narration_offset_s": 0.0,
            }
        )
        for i in range(3)
    ]


# A valid 2-shot list (<=2 consecutive same-source passes) for the
# render_shot_list-level forwarding tests, where _render_pass is stubbed.
_SL2 = {
    "version": 1,
    "aspect": "16:9",
    "total_duration_s": 6.0,
    "shots": [
        {"idx": 0, "duration_s": 3.0, "intent": "x", "source": "image_gen",
         "prompt": "p0", "narration_offset_s": 0.0},
        {"idx": 1, "duration_s": 3.0, "intent": "x", "source": "image_gen",
         "prompt": "p1", "narration_offset_s": 0.0},
    ],
    "director_model": "ollama/test",
    "director_prompt_version": "v1",
    "director_decided_at": "2026-06-08T00:00:00+00:00",
}


def _ok_shot():
    return slr.ShotRenderResult(
        idx=0, source="image_gen", success=True, clip_path="/tmp/c.png", duration_s=3.0
    )


async def test_render_pass_emits_position_pct_per_shot():
    calls = []

    async def cb(step, pct):
        calls.append((step, pct))

    shots = _shots3()
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=_ok_shot())):
        states = await slr._render_pass(shots, render_kwargs={}, progress_cb=cb)
    assert len(states) == 3
    # honest position-pct: round(100*i/3), 3/3 clamped to 99 (finish flips done)
    assert calls == [("shot 1/3", 33), ("shot 2/3", 67), ("shot 3/3", 99)]


async def test_render_pass_survives_a_raising_progress_cb():
    async def bad_cb(step, pct):
        raise RuntimeError("cb boom")

    shots = _shots3()
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=_ok_shot())):
        states = await slr._render_pass(shots, render_kwargs={}, progress_cb=bad_cb)
    assert len(states) == 3  # all shots rendered despite the callback raising


async def test_render_pass_no_cb_is_backcompat():
    shots = _shots3()
    with patch.object(slr, "_render_one_shot", AsyncMock(return_value=_ok_shot())):
        states = await slr._render_pass(shots, render_kwargs={})
    assert len(states) == 3


async def test_render_shot_list_forwards_progress_cb():
    async def cb(step, pct):
        return None

    sl = VideoShotList.model_validate(_SL2)
    with patch.object(slr, "_render_pass", AsyncMock(return_value=[])) as rp:
        await slr.render_shot_list(
            post_id="p",
            shot_list=sl,
            audio_path="",
            output_path="/tmp/o.mp4",
            image_gen_url="http://ig:9836",
            site_config=None,
            progress_cb=cb,
        )
    assert rp.await_args.kwargs["progress_cb"] is cb


async def test_render_shot_list_no_progress_cb_passes_none():
    sl = VideoShotList.model_validate(_SL2)
    with patch.object(slr, "_render_pass", AsyncMock(return_value=[])) as rp:
        result = await slr.render_shot_list(
            post_id="p",
            shot_list=sl,
            audio_path="",
            output_path="/tmp/o.mp4",
            image_gen_url="http://ig:9836",
            site_config=None,
        )
    assert rp.await_args.kwargs["progress_cb"] is None
    # no rendered shots -> honest failure, compositor never reached
    assert result.success is False
