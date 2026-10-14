"""Smoother cuts in cartoons: a dissolve between story scenes, a quick one between shots.

Matt on the Midnight Patch rough cut (2026-10-14): "need to have smoother
transitions between scenes and clips". The compositor centres each transition
on its cut by holding the frames either side, so the length, the captions and
the lip-sync stay where they were.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poindexter.plugins.media_compositor import CompositionScene
from poindexter.services.media_compositors import ffmpeg_local as ff
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr

TRANSITIONS = {"scene_kind": "dissolve", "scene_s": 0.6, "shot_s": 0.2}


class TestPlan:
    def test_a_transition_is_clamped_to_the_shorter_side(self):
        scenes = [CompositionScene(clip_path="a", duration_s=2.0),
                  CompositionScene(clip_path="b", duration_s=0.1, transition_in="dissolve", transition_s=0.6),
                  CompositionScene(clip_path="c", duration_s=3.0, transition_in="fade_black", transition_s=0.6),
                  CompositionScene(clip_path="d", duration_s=3.0, transition_in="", transition_s=0.6)]
        assert ff.plan_transitions(scenes) == [("", 0.0), ("fade", 0.1), ("fadeblack", 0.1), ("", 0.0)]

    def test_the_join_keeps_the_total_length(self):
        cmd = ff._build_transition_join_cmd(
            binary="ffmpeg", paths=["a.mp4", "b.mp4", "c.mp4"], durations=[2.0, 1.5, 3.5],
            transitions=[("", 0.0), ("fade", 0.4), ("", 0.0)], output_path="o.mp4", fps=30,
            encoder="libx264", preset="fast", crf=23, audio_bitrate="192k", loglevel="error",
        )
        graph = cmd[cmd.index("-filter_complex") + 1]
        # The cut at 2.0 s: each side holds 0.2 s and the 0.4 s dissolve starts at 2.0 - 0.2.
        assert "[0:v]tpad=start_mode=clone:start_duration=0.000:stop_mode=clone:stop_duration=0.200" in graph
        assert "[1:v]tpad=start_mode=clone:start_duration=0.200:stop_mode=clone:stop_duration=0.000" in graph
        assert "xfade=transition=fade:duration=0.400:offset=1.800" in graph
        assert "[j1][c2]concat=n=2:v=1:a=0[j2]" in graph
        assert cmd[cmd.index("-t", cmd.index("-filter_complex")) + 1] == "7.000"


class TestRenderer:
    def test_settings_drive_the_transitions(self):
        got = slr.transition_settings(SiteConfig(initial_config={"video_scene_transition": "fade_black",
                                                                 "video_shot_transition_seconds": "0"}))
        assert got == {"scene_kind": "fade_black", "scene_s": 0.6, "shot_s": 0.0}

    @pytest.mark.parametrize(("prev", "now", "want"), [
        (None, "s1", ("", 0.0)), ("s1", "s1", ("dissolve", 0.2)), ("s1", "s2", ("dissolve", 0.6)),
        ("s1", None, ("", 0.0)),
    ])
    def test_scene_changes_dissolve_and_shots_soften(self, prev, now, want):
        assert slr.transition_into(prev, now, TRANSITIONS) == want

    @pytest.mark.asyncio
    async def test_only_a_slots_first_scene_carries_it_and_other_videos_keep_hard_cuts(self):
        results = [slr.ShotRenderResult(idx=i, source="image_kenburns", success=True, clip_path=f"/w/s{i}.png")
                   for i in range(3)]
        cartoon = SimpleNamespace(shots=[SimpleNamespace(prompt="p", scene_id=s) for s in ("s1", "s1", "s2")])
        scenes = await slr._scenes_for_plan([(0, 2.0), (1, 2.0), (2, 2.0)], results, cartoon, width=8, height=8,
                                            transitions=TRANSITIONS)
        assert [(s.transition_in, s.transition_s) for s in scenes] == [("", 0.0), ("dissolve", 0.2), ("dissolve", 0.6)]
        blog = SimpleNamespace(shots=[SimpleNamespace(prompt="p") for _ in range(3)])
        scenes = await slr._scenes_for_plan([(0, 2.0), (1, 2.0), (2, 2.0)], results, blog, width=8, height=8,
                                            transitions=TRANSITIONS)
        assert all(not s.transition_in for s in scenes)
