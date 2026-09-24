"""A video clip shorter than its scene plays ONCE, then continues on its final frame.

The compositor feeds any short clip through ``-stream_loop -1``. A hero clip
is 81 frames at 16 fps (~5.06 s), so in an 18 s scene it played three and a
half times: the "single shot repeating" the operator flagged on 2026-09-23.
``_scenes_for_plan`` splits such a slot into the clip once plus a slow centred
push on its last frame. Scenes are concatenated with hard cuts (no overlap),
so the split must preserve every slot's duration exactly or the narration
drifts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from poindexter.services.media_compositors.ffmpeg_local import KEN_BURNS_CENTER
from poindexter.services.video_renderers import shot_list_renderer as slr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _results(*specs: tuple[str, str]) -> list[slr.ShotRenderResult]:
    return [
        slr.ShotRenderResult(idx=i, source=source, success=True, clip_path=path)
        for i, (source, path) in enumerate(specs)
    ]


def _shot_list(n: int, prompt: str = "flat vector illustration, a data centre at night"):
    return SimpleNamespace(shots=[SimpleNamespace(prompt=prompt) for _ in range(n)])


class _Calls:
    def __init__(self, clip_s: float | None = 5.06, still: str | None = "auto"):
        self.clip_s, self.still = clip_s, still
        self.probed: list[str] = []
        self.grabbed: list[str] = []

    async def probe(self, path: str) -> float | None:
        self.probed.append(path)
        return self.clip_s

    async def grab(self, path: str, *, width: int, height: int) -> str | None:
        self.grabbed.append(path)
        return path.replace(".mp4", "_lastframe.png") if self.still == "auto" else self.still


async def _scenes(plan, results, calls, n=None):
    return await slr._scenes_for_plan(
        plan, results, _shot_list(n or len(results)), width=1920, height=1080,
        probe=calls.probe, grab=calls.grab,
    )


async def test_a_short_hero_plays_once_then_continues_on_its_last_frame():
    calls = _Calls(clip_s=5.06)
    scenes = await _scenes([(0, 18.0)], _results(("generative", "/w/shot_00.mp4")), calls)

    clip, still = scenes
    assert (clip.clip_path, clip.duration_s, clip.hold_last_frame) == ("/w/shot_00.mp4", 5.06, True)
    assert still.clip_path == "/w/shot_00_lastframe.png"
    assert still.ken_burns_variant == KEN_BURNS_CENTER
    # Hard-cut concat: the slot's time must survive the split exactly.
    assert clip.duration_s + still.duration_s == pytest.approx(18.0)


async def test_short_stock_footage_is_continued_too():
    calls = _Calls(clip_s=8.0)
    scenes = await _scenes([(0, 14.0)], _results(("pexels", "/w/shot_00_pexels.mp4")), calls)
    assert [s.duration_s for s in scenes] == [8.0, 6.0]


async def test_a_clip_that_fills_its_slot_stays_one_scene():
    calls = _Calls(clip_s=18.0)
    scenes = await _scenes([(0, 18.0)], _results(("pexels", "/w/p.mp4")), calls)
    assert len(scenes) == 1 and scenes[0].duration_s == 18.0
    assert calls.grabbed == []


async def test_a_sliver_of_overhang_is_left_to_the_compositor():
    calls = _Calls(clip_s=17.8)
    scenes = await _scenes([(0, 18.0)], _results(("generative", "/w/g.mp4")), calls)
    assert len(scenes) == 1


async def test_stills_are_never_probed():
    calls = _Calls()
    scenes = await _scenes([(0, 12.0)], _results(("image_kenburns", "/w/shot_00.png")), calls)
    assert len(scenes) == 1 and calls.probed == []


async def test_a_presenter_clip_keeps_holding_its_last_frame():
    calls = _Calls()
    scenes = await _scenes([(0, 9.0)], _results(("presenter", "/w/presenter_0.mp4")), calls)
    assert len(scenes) == 1 and scenes[0].hold_last_frame is True
    assert calls.probed == []


async def test_a_failed_grab_or_probe_keeps_the_old_single_scene():
    for calls in (_Calls(still=None), _Calls(clip_s=None)):
        scenes = await _scenes([(0, 18.0)], _results(("generative", "/w/g.mp4")), calls)
        assert len(scenes) == 1 and scenes[0].duration_s == 18.0


async def test_a_cycled_plan_probes_and_grabs_each_clip_once():
    calls = _Calls(clip_s=5.0)
    plan = [(0, 12.0), (1, 6.0), (0, 12.0)]
    results = _results(("generative", "/w/a.mp4"), ("image_kenburns", "/w/b.png"))
    scenes = await _scenes(plan, results, calls)
    assert calls.probed == ["/w/a.mp4"] and calls.grabbed == ["/w/a.mp4"]
    assert len(scenes) == 5
    assert sum(s.duration_s for s in scenes) == pytest.approx(30.0)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg on PATH")
async def test_the_last_frame_is_fitted_like_the_compositor_fits_the_clip(tmp_path):
    clip = str(tmp_path / "hero.mp4")
    # An 832x480 clip, the hero plate: letterboxed into 1920x1080, not cropped.
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=832x480:rate=16:duration=2", "-pix_fmt", "yuv420p", clip],
        check=True,
    )
    still = await slr._last_frame_still(clip, width=1920, height=1080)
    assert still and os.path.getsize(still) > 0
    from PIL import Image

    with Image.open(still) as img:
        assert img.size == (1920, 1080)
        assert img.getpixel((5, 540))[:3] == (0, 0, 0)  # the pad bar, as in the clip scene
