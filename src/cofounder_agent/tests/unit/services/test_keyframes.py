"""Keyframes: an action shot is two drawings of its moment, joined by a dissolve, flicker or cut.

Matt on The Battery Drop (2026-10-15): "every movement is hallucinated", and
"the talking head animations aren't bad... the action animation are where it
is weakest". He proposed limited animation: one picture before and one after,
the movement shown through the transition (a raised eyebrow strobing, a slow
dissolve into an explosion). Nothing between the two drawings is generated.

Pinned here: the director gives action shots the keyframes source by
default (``story_action_motion``; "animate" keeps the video model), asks the
illustrator for each one's change and transition, and holds a shot as a still
when no change was planned; a storyboard keeps the stills a storyboard drew
instead of turning them into animated shots; the renderer's schedule for
each transition; the second drawing is the approved one when the storyboard
drew it, else an edit of the first; the clip cache keys on the second drawing.
"""

from __future__ import annotations

import json
import shutil
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import clip_cache
from poindexter.services.video_renderers import shot_list_renderer as slr


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.nova.portrait_url": "https://cdn/nova.png",
        "video_presenter_max_chunks_per_shot": "1",
        "video_comfyui_s2v_length_frames": "77", "video_comfyui_fps": "16",
        "story_max_shot_seconds": "6", "story_min_shot_seconds": "0.8",
        "story_min_insert_seconds": "2.5", "story_min_reaction_seconds": "1.0",
        "story_establishing_seconds": "0", "story_max_props": "0",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


SCREENPLAY = {
    "title": "t", "logline": "l", "cast": ["dex", "nova"],
    "scenes": [{"scene_id": "s1", "setting": "a lab", "characters": ["dex", "nova"], "beats": [
        {"kind": "action", "text": "Dex drops the cup", "characters": ["dex"], "seconds": 4},
        {"kind": "line", "speaker": "nova", "text": "Oh no.", "delivery": "dry"},
    ]}],
}
TIMELINE = [
    {"scene_id": "s1", "kind": "action", "text": "Dex drops the cup", "characters": ["dex"], "start_s": 0.0, "end_s": 4.0},
    {"scene_id": "s1", "kind": "line", "speaker": "nova", "text": "Oh no.", "start_s": 4.0, "end_s": 5.5},
]


def _shot(**extra) -> Shot:
    return Shot(idx=0, duration_s=4.0, intent="i", source="keyframes", prompt="dex holds a cup",
                narration_offset_s=0.0, after_prompt="the cup now shattered on the floor", transition="cut", **extra)


class TestSchema:
    def test_a_keyframes_shot_needs_both_drawings(self):
        with pytest.raises(ValueError, match="after_prompt"):
            Shot(idx=0, duration_s=4.0, intent="i", source="keyframes", prompt="p", narration_offset_s=0.0)
        assert _shot().transition == "cut"


class TestStreak:
    def test_keyframes_moments_in_a_row_are_a_scene_in_motion_not_a_repeat(self):
        """Circuit and the Song's storyboard (2026-10-15) failed: three keyframes
        shots in a row read as one source repeated."""
        from datetime import datetime, timezone

        from poindexter.schemas.video_shot_list import VideoShotList

        shots = [_shot().model_copy(update={"idx": i, "prompt": f"moment {i}", "narration_offset_s": 4.0 * i}).model_dump()
                 for i in range(4)]
        VideoShotList.model_validate({"aspect": "16:9", "timing": "exact", "total_duration_s": 16.0, "shots": shots,
                                      "director_model": "m", "director_prompt_version": "v",
                                      "director_decided_at": datetime.now(timezone.utc)})


class TestDirector:
    @pytest.mark.parametrize(("mode", "source"), [(None, "keyframes"), ("animate", "generative")])
    def test_action_shots_are_keyframes_unless_the_studio_animates(self, mode, source):
        sc = _sc(**({"story_action_motion": mode} if mode else {}))
        plan = sd.assign_sources(sd.plan_shots(SCREENPLAY, TIMELINE, 5.5, sc), sc)
        assert plan[0]["source"] == source
        assert plan[1]["source"] == "presenter"  # talking shots stay lip-synced

    @pytest.mark.asyncio
    async def test_the_illustrator_gives_each_its_change_and_transition(self, monkeypatch):
        import poindexter.services.llm_text as llm_text

        async def reply(prompt, **kw):
            assert "two drawings" in prompt
            return json.dumps({"shots": [{"id": 0, "prompt": "dex holds a cup", "after": "the cup now shattered",
                                          "transition": "slam"}]})

        monkeypatch.setattr(llm_text, "ollama_chat_text", reply)
        monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "m")
        listing = await sd.direct_screenplay(SCREENPLAY, TIMELINE, 5.5, site_config=_sc(), style="")
        first = listing["shots"][0]
        assert (first["source"], first["after_prompt"], first["transition"]) == ("keyframes", "the cup now shattered", "dissolve")

    @pytest.mark.asyncio
    async def test_a_shot_with_no_change_planned_is_held_as_a_still(self, monkeypatch):
        import poindexter.services.llm_text as llm_text

        monkeypatch.setattr(llm_text, "ollama_chat_text",
                            AsyncMock(return_value=json.dumps({"shots": [{"id": 0, "prompt": "dex holds a cup"}]})))
        monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "m")
        listing = await sd.direct_screenplay(SCREENPLAY, TIMELINE, 5.5, site_config=_sc(), style="")
        assert listing["shots"][0]["source"] == "image_kenburns"

    def test_a_storyboard_keeps_its_stills_in_keyframes_mode(self):
        listing = {"shots": [{"idx": 0, "source": "image_kenburns", "prompt": "p", "duration_s": 4.0}]}
        rows = [{"idx": 0, "image_url": "https://cdn/0.png"}]
        out = sd.prepare_storyboard_render(listing, rows, _sc())
        assert out["shots"][0]["source"] == "image_kenburns" and out["shots"][0]["frame_url"] == "https://cdn/0.png"
        animated = sd.prepare_storyboard_render(listing, rows, _sc(story_action_motion="animate"))
        assert animated["shots"][0]["source"] == "generative"


class TestRenderer:
    @pytest.mark.parametrize(("transition", "want"), [
        ("cut", [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0]),
        ("dissolve", [0.0, 0.0, 0.0, 0.17, 0.39, 0.61, 0.83, 1.0, 1.0, 1.0]),
    ])
    def test_the_schedule_holds_then_joins(self, transition, want):
        assert [round(x, 2) for x in slr.keyframe_schedule(transition, 1.0, 10)] == want

    def test_a_toggle_flickers_after_a_held_start(self):
        got = slr.keyframe_schedule("toggle", 2.0, 10)
        assert got[:4] == [0.0] * 4 and set(got[4:]) == {0.0, 1.0}

    @pytest.mark.asyncio
    async def test_the_second_drawing_is_the_approved_one_else_an_edit_of_the_first(self, monkeypatch, tmp_path):
        from PIL import Image

        before = tmp_path / "before.png"
        Image.new("RGB", (64, 36), "blue").save(before)
        approved = AsyncMock(return_value=str(tmp_path / "after.png"))
        monkeypatch.setattr(slr, "_approved_frame", approved)
        edit = AsyncMock(return_value=(True, ""))
        monkeypatch.setattr("poindexter.services.reference_frames.render_reference_frame", edit)
        assert await slr.draw_keyframe_after(_shot(after_url="https://cdn/a.png"), before=str(before),
                                             output_path=str(tmp_path / "after.png"), site_config=_sc())
        edit.assert_not_awaited()
        assert approved.await_args.args[0].frame_url == "https://cdn/a.png"
        assert await slr.draw_keyframe_after(_shot(), before=str(before), output_path=str(tmp_path / "b.png"),
                                             site_config=_sc())
        kw = edit.await_args.kwargs
        assert kw["reference_paths"] == [str(before)] and (kw["width"], kw["height"]) == (64, 36)
        assert "The only change: the cup now shattered on the floor. Everyone in the picture appears once" in kw["prompt"]

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
    def test_the_clip_joins_the_two_drawings(self, tmp_path):
        from PIL import Image

        a, b = tmp_path / "a.png", tmp_path / "b.png"
        Image.new("RGB", (64, 36), "blue").save(a)
        Image.new("RGB", (64, 36), "red").save(b)
        assert slr._write_keyframes_clip(str(a), str(b), "cut", 1.0, str(tmp_path / "o.mp4"), 10)

    def test_the_clip_cache_keys_on_the_second_drawing(self):
        assert clip_cache.shot_key(_shot()) != clip_cache.shot_key(_shot().model_copy(update={"after_prompt": "x"}))
        assert clip_cache.shot_key(_shot()) != clip_cache.shot_key(_shot().model_copy(update={"transition": "toggle"}))


def test_pack_prompt_explains_two_drawings():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt("story.shot_visuals", title="t", logline="l", shot_rows="- id 1: x",
                                           props_block="(none)")
    assert '"after"' in text and '"transition"' in text and "two drawings" in text



@pytest.mark.asyncio
async def test_the_storyboard_draws_and_keeps_the_second_drawing(monkeypatch, tmp_path):
    from datetime import datetime, timezone

    from poindexter.services import storyboard as sb

    listing = {
        "aspect": "16:9", "timing": "exact", "total_duration_s": 4.0, "director_model": "m",
        "director_prompt_version": "v", "director_decided_at": datetime.now(timezone.utc).isoformat(),
        "shots": [_shot().model_dump(mode="json")],
    }

    async def draw(shot, *, work, ctx, site_config, portraits):
        path = work / "before.png"
        path.write_bytes(b"png")
        return str(path)

    after = AsyncMock(return_value=True)
    monkeypatch.setattr(sb, "_draw", draw)
    monkeypatch.setattr(sb, "_upload", AsyncMock(side_effect=lambda local, key, mime, sc: f"https://cdn/{key}"))
    monkeypatch.setattr(slr, "draw_keyframe_after", after)
    monkeypatch.setattr(slr, "reference_context_for", lambda shot_list: None)
    monkeypatch.setattr("poindexter.services.gpu_scheduler.gpu._unload_comfyui", AsyncMock())
    urls = await sb._draw_shots(listing, None, work=tmp_path, site_config=_sc(), pool=None,
                                upload_prefix="drafts/x", version_tag="")
    assert urls == {0: "https://cdn/drafts/x/shot_00.png"}
    assert listing["shots"][0]["after_url"] == "https://cdn/drafts/x/shot_00_after.png"
    assert after.await_args.kwargs["before"] == str(tmp_path / "before.png")
    row = sb._rows(listing, [], urls)[0]
    assert (row["after_url"], row["transition"]) == ("https://cdn/drafts/x/shot_00_after.png", "cut")


class TestReviseTheSecondDrawing:
    """The Circuit and the Song (2026-10-15): two "after" drawings were wrong (a second
    Cinder, an orange room) and a revision note could only redraw the first."""

    def test_a_note_can_replace_the_second_drawing(self):
        from poindexter.services import storyboard as sb

        listing = {"shots": [{**_shot().model_dump(mode="json"), "after_url": "https://cdn/old_after.png"}]}
        assert "| then: the cup now shattered on the floor" in sb.revision_table(
            [{"idx": 0, "kind": sb.STILL, "characters": [], "picture": "dex holds a cup"}], {0}, listing)
        assert sb.check_revision({"shots": [{"id": 0, "after": "the cup on the floor in two pieces"}]}, {0}) == []
        redrawn, changed = sb.apply_revision(listing, {"shots": [{"id": 0, "after": "the cup on the floor in two pieces"}]})
        assert redrawn == {0} == changed
        assert listing["shots"][0]["after_prompt"] == "the cup on the floor in two pieces"
        assert "after_url" not in listing["shots"][0]

    def test_the_template_migration_moves_the_old_default(self):
        import importlib
        import pathlib

        from poindexter.services.settings_defaults import DEFAULTS

        path = next(pathlib.Path(slr.__file__).parents[1].joinpath("migrations").glob("*keyframe_second_drawing*.py"))
        mod = importlib.import_module(f"poindexter.services.migrations.{path.stem}")
        assert mod.NEW == DEFAULTS["story_keyframe_after_template"] and "no one is added" in mod.NEW
