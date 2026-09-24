"""Stock footage is judged on FIT to the video, not on matching its search words.

2026-09-24, f555bedc: the shot judge compared stock clips with their own search
words, so blockchain node logs ("code scrolling"), street bokeh ("blurred
lights") and a mostly black glitch clip ("screen noise") all scored 92. The
stock judge now sees the video topic and the narration under the shot, samples
several frames across the part that plays (worst wins), scores black frames 0
without a model call, and lets its fit LABEL cap the score: on the calibration
clips the labels were right 4/4 while "loose" still came back as 65.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_vision_qa as qa_mod
from poindexter.services.video_renderers.shot_vision_qa import score_shot_frame

pytestmark = pytest.mark.asyncio

_DISPATCH = "poindexter.services.llm_providers.dispatcher.dispatch_complete"


def _stock(duration: float = 6.0) -> Shot:
    return Shot(
        idx=3, duration_s=duration, intent="the sync process", source="pexels",
        query="close up of computer code scrolling on monitor", narration_offset_s=0.0,
    )


def _sc(**over):
    cfg = {"qa_vision_model": "ollama/qwen3-vl:30b"}
    cfg.update(over)
    return SiteConfig(initial_config=cfg)


def _completion(text: str):
    from poindexter.plugins.llm_provider import Completion

    return Completion(text=text, model="qwen3-vl:30b")


def _png(path, color=(40, 120, 160)):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 36), color)
    ImageDraw.Draw(img).rectangle((10, 8, 50, 28), fill=(220, 220, 220))
    img.save(path)
    return str(path)


def _prompt_of(dispatch: AsyncMock) -> str:
    return dispatch.call_args.args[1][0]["content"][0]["text"]


async def test_the_stock_judge_sees_the_topic_and_the_narration(tmp_path):
    dispatch = AsyncMock(return_value=_completion('{"fit": "fits", "score": 88, "reason": "racks"}'))
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=_png(tmp_path / "shot_03_sub.png"), shot=_stock(), site_config=_sc(),
            pool=object(), topic="Skip NCCL: LoRA adapter syncing",
            narration="a small proxy routes requests to the newest replica",
        )
    assert res.score == 88.0 and res.fit == "fits"
    prompt = _prompt_of(dispatch)
    assert "Skip NCCL: LoRA adapter syncing" in prompt
    assert "a small proxy routes requests to the newest replica" in prompt
    assert "SEARCH WORDS USED TO FIND IT: close up of computer code scrolling on monitor" in prompt


@pytest.mark.parametrize(("label", "raw", "capped"), [("loose", 65, 45.0), ("off", 70, 20.0), ("fits", 91, 91.0)])
async def test_the_fit_label_caps_the_score(tmp_path, label, raw, capped):
    body = f'{{"fit": "{label}", "score": {raw}, "reason": "r"}}'
    with patch(_DISPATCH, AsyncMock(return_value=_completion(body))):
        res = await score_shot_frame(
            frame_path=_png(tmp_path / "f.png"), shot=_stock(), site_config=_sc(), pool=object(),
        )
    assert res.score == capped
    assert res.score < 60 or label == "fits"  # loose/off always land under the escalation threshold


async def test_a_black_frame_scores_zero_without_a_model_call(tmp_path):
    from PIL import Image

    Image.new("RGB", (64, 36), (2, 2, 2)).save(tmp_path / "void.png")
    dispatch = AsyncMock()
    with patch(_DISPATCH, dispatch):
        res = await score_shot_frame(
            frame_path=str(tmp_path / "void.png"), shot=_stock(), site_config=_sc(), pool=object(),
        )
    assert res.score == 0.0 and "black" in res.reason and res.fit == "off"
    dispatch.assert_not_awaited()


async def test_frames_span_the_played_part_and_the_worst_wins(tmp_path):
    frames = [_png(tmp_path / f"k{k}.png") for k in range(3)]
    grabbed: list[float] = []

    async def fake_frame_at(path, at_s, tag):
        grabbed.append(round(at_s, 2))
        return frames[len(grabbed) - 1]

    replies = [
        '{"fit": "fits", "score": 90, "reason": "a"}',
        '{"fit": "loose", "score": 65, "reason": "b"}',
        '{"fit": "fits", "score": 88, "reason": "c"}',
    ]
    dispatch = AsyncMock(side_effect=[_completion(r) for r in replies])
    with patch(_DISPATCH, dispatch), \
         patch.object(qa_mod, "_probe_seconds", AsyncMock(return_value=9.0)), \
         patch.object(qa_mod, "_frame_at", fake_frame_at):
        res = await score_shot_frame(
            frame_path="/w/shot_03_pexels.mp4", shot=_stock(duration=6.0), site_config=_sc(),
            pool=object(),
        )
    # A 9 s clip in a 6 s shot: only the first 6 s play, sampled at 1/3/5 s.
    assert grabbed == [1.0, 3.0, 5.0]
    assert res.score == 45.0 and res.fit == "loose"


async def test_ai_shots_keep_the_quality_judge(tmp_path):
    shot = Shot(idx=0, duration_s=4.0, intent="opening payoff", source="image_gen",
                prompt="a cyan circuit board", narration_offset_s=0.0)
    dispatch = AsyncMock(return_value=_completion('{"score": 82, "reason": "sharp"}'))
    with patch(_DISPATCH, dispatch):
        await score_shot_frame(
            frame_path=_png(tmp_path / "a.png"), shot=shot,
            site_config=_sc(video_shot_qa_crop_enabled="false"), pool=object(),
            topic="anything", narration="anything",
        )
    assert "SHOT INTENT" in _prompt_of(dispatch)
    assert "SEARCH WORDS USED TO FIND IT" not in _prompt_of(dispatch)


# ---------------------------------------------------------------------------
# renderer side: what the judge is told
# ---------------------------------------------------------------------------


def _shot(idx: int, offset: float, duration: float) -> Shot:
    return Shot(idx=idx, duration_s=duration, intent="i", source="image_kenburns",
                prompt="p", narration_offset_s=offset)


async def test_narration_is_scaled_from_the_plan_onto_the_voice():
    from poindexter.services.video_renderers.shot_list_renderer import _narration_by_shot

    shots = [_shot(0, 0.0, 10.0), _shot(1, 10.0, 10.0)]  # a 20 s plan...
    cues = [(0.0, 4.0, "a b"), (4.0, 9.0, "c d"), (9.0, 12.0, "e f"), (12.0, 18.0, "g h")]
    # ...over an 18 s narration: windows [0, 9] and [9, 18].
    got = _narration_by_shot(shots, cues, narration_s=18.0)
    assert got == {0: "a b c d", 1: "e f g h"}
    assert _narration_by_shot(shots, [], narration_s=18.0) == {}


async def test_the_topic_is_the_post_title_and_a_failed_lookup_is_empty():
    from poindexter.services.video_renderers.shot_list_renderer import _video_topic

    class _Pool:
        def __init__(self, row=None, exc=None):
            self.row, self.exc = row, exc

        async def fetchrow(self, sql, task_id):
            if self.exc:
                raise self.exc
            return self.row

    assert await _video_topic(_Pool(row={"topic": "Skip NCCL"}), "t1") == "Skip NCCL"
    assert await _video_topic(_Pool(exc=RuntimeError("db down")), "t1") == ""
    assert await _video_topic(None, "t1") == ""
