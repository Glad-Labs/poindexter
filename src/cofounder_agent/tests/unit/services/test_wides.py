"""Wides: each scene opens on one, a wide is a camera move, and its lettering is the plate's.

Midnight Patch's full render (2026-10-14) had five 1.5-1.8 s copies of one
trio wide between close-ups. Animated, they drifted (Mara turned into a
mouse); the shot judge capped them at 45 for scrambled sign lettering that
comes from the location's plate, every re-roll scored 45 again, and each was
then replaced by the close-up before it held over. Matt: a wide can be a
zoom and slow pan across the scene under the music.

Pinned here: a scene opens on a landscape (the wider world, nobody in it,
not the plate the characters stand in) held ``story_establishing_seconds``
under its first beat; a short gap after a line is a cutaway to the
listeners, not another copy of the master; a wide is a Ken Burns still
unless ``story_wide_motion`` is "animate"; and the judge's own score stands
for a plate-drawn shot it says has garbled text.
"""

from __future__ import annotations

import pytest

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_vision_qa as sq


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.nova.portrait_url": "https://cdn/nova.png",
        "video_presenter_max_chunks_per_shot": "1",
        "video_comfyui_s2v_length_frames": "77",
        "video_comfyui_fps": "16",
        "story_max_shot_seconds": "6",
        "story_min_shot_seconds": "0.8",
        "story_min_insert_seconds": "2.5",
        "story_min_reaction_seconds": "1.0",
        "story_establishing_seconds": "3.0",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


SCREENPLAY = {
    "title": "t", "cast": ["dex", "nova"],
    "scenes": [
        {"scene_id": "s1", "setting": "a lab", "characters": ["dex", "nova"], "beats": []},
        {"scene_id": "s2", "setting": "a roof", "characters": ["dex", "nova"], "beats": []},
    ],
}


def _line(scene, speaker, start, end):
    return {"scene_id": scene, "kind": "line", "speaker": speaker, "text": f"{speaker} at {start}",
            "start_s": start, "end_s": end}


def _timeline():
    return [
        _line("s1", "dex", 0.0, 6.0),     # long enough to share with the wide
        _line("s1", "nova", 6.0, 12.0),
        _line("s2", "nova", 12.0, 14.5),  # too short to share: the wide holds it
        _line("s2", "dex", 14.5, 20.5),
    ]


def _plan(**extra):
    return sd.assign_sources(sd.plan_shots(SCREENPLAY, _timeline(), 20.5, _sc(**extra)), _sc(**extra))


class TestEstablishing:
    def test_each_scene_opens_on_a_landscape_with_nobody_in_it(self):
        plan = _plan()
        first = {s["scene_id"]: s for s in reversed(plan)}
        assert first["s1"]["kind"] == "landscape" and first["s1"]["length"] == 3.0
        assert first["s2"]["kind"] == "landscape" and first["s2"]["start_s"] == 12.0
        assert first["s1"]["characters"] == [] and first["s1"]["source"] == "image_kenburns"
        talk = next(s for s in plan if s["kind"] == "talk")
        assert talk["start_s"] == 3.0 and talk["speaker"] == "dex"  # the line goes on under the cut

    def test_a_short_first_line_plays_over_the_wide(self):
        s2 = [s for s in _plan() if s["scene_id"] == "s2"]
        assert s2[0]["kind"] == "landscape" and s2[0]["length"] == 2.5
        assert s2[1]["kind"] == "talk" and s2[1]["speaker"] == "dex"

    def test_the_track_is_still_tiled_exactly(self):
        plan = _plan()
        assert plan[0]["start_s"] == 0.0
        for a, b in zip(plan, plan[1:], strict=False):
            assert abs(a["start_s"] + a["length"] - b["start_s"]) < 1e-6
        assert abs(plan[-1]["start_s"] + plan[-1]["length"] - 20.5) < 1e-6

    def test_zero_turns_it_off(self):
        assert _plan(story_establishing_seconds="0")[0]["kind"] == "talk"


class TestWides:
    def test_a_short_gap_after_a_line_is_a_cutaway_not_the_master(self):
        plan = _plan()
        assert not [s for s in plan if s["kind"] == "master"]
        assert any(s["kind"] == "reaction" for s in plan)

    def test_a_wide_is_a_camera_move_unless_animated(self):
        timeline = [*_timeline()[:1], {"scene_id": "s1", "kind": "line", "speaker": "narrator", "text": "Later.",
                                       "start_s": 6.0, "end_s": 7.5},
                    _line("s1", "nova", 7.5, 12.0)]
        for motion, source in (("camera", "image_kenburns"), ("animate", "generative")):
            sc = _sc(story_wide_motion=motion)
            plan = sd.assign_sources(sd.plan_shots(SCREENPLAY, timeline, 12.0, sc), sc)
            assert {s["source"] for s in plan if s["kind"] == "master"} == {source}
            assert {s["source"] for s in plan if s["kind"] == "landscape"} == {"image_kenburns"}

    def test_the_landscape_is_drawn_on_its_own_not_in_the_plate(self):
        listing = sd.build_shot_list(_plan(), {}, style="ink", director_model="m", site_config=_sc())
        first = listing["shots"][0]
        assert first["source"] == "image_kenburns" and not first.get("establishing")
        assert first.get("location_id") is None and first["characters"] == []
        assert "seen from far away" in first["prompt"] and first["scene_id"] == "s1"


def _shot(**kw):
    return Shot(idx=0, duration_s=3.0, intent="x", source="image_kenburns", prompt="a roof",
                narration_offset_s=0.0, **kw)


class TestPlateLettering:
    @pytest.fixture
    def judge(self, monkeypatch, tmp_path):
        frame = tmp_path / "f.png"
        frame.write_bytes(b"png")

        async def score(image_path, *, prompt, model, pool, shot_idx):
            return sq.ShotQAResult(score=72.0, reason="neon signs carry scrambled letters", text="garbled")

        monkeypatch.setattr(sq, "_score_image", score)
        return str(frame)

    def _cfg(self, **extra):
        return SiteConfig(initial_config={"qa_vision_model": "m", "video_shot_qa_crop_enabled": "false", **extra})

    @pytest.mark.asyncio
    async def test_a_plate_drawn_shot_keeps_the_judges_score(self, judge):
        r = await sq.score_shot_frame(frame_path=judge, shot=_shot(location_id="roof"), site_config=self._cfg(), pool=object())
        assert r.score == 72.0 and r.text == "garbled"

    @pytest.mark.asyncio
    async def test_any_other_shot_is_still_capped(self, judge):
        r = await sq.score_shot_frame(frame_path=judge, shot=_shot(), site_config=self._cfg(), pool=object())
        assert r.score == 45.0 and r.reason.startswith("garbled text")

    @pytest.mark.asyncio
    async def test_the_cap_can_be_turned_back_on(self, judge):
        r = await sq.score_shot_frame(frame_path=judge, shot=_shot(scene_id="s1"),
                                      site_config=self._cfg(video_shot_qa_cap_plate_text="true"), pool=object())
        assert r.score == 45.0


class TestTalkingCoverage:
    def test_a_speakers_lines_rotate_through_the_framings(self):
        """Every line was the same frontal portrait (Matt, 2026-10-14: "It feels
        flat just going from portrait to portrait")."""
        timeline = [_line("s1", "dex", 0.0, 4.0), _line("s1", "nova", 4.0, 8.0),
                    _line("s1", "dex", 8.0, 12.0), _line("s1", "dex", 12.0, 16.0)]
        sc = _sc(story_establishing_seconds="0")
        listing = sd.build_shot_list(sd.assign_sources(sd.plan_shots(SCREENPLAY, timeline, 16.0, sc), sc), {},
                                     style="ink", director_model="m", site_config=sc)
        dex = [s["frame_prompt"] for s in listing["shots"] if s.get("speaker") == "dex"]
        assert "close-up of Dex" in dex[0] and "medium shot of Dex" in dex[1]
        assert len(set(dex)) == len(dex)
        assert all("alone in the frame and talking" in p for p in dex)

    def test_the_framings_are_a_setting(self):
        sc = _sc(story_talking_framings='["profile of {name}"]')
        assert sd._talking_framings(sc) == ["profile of {name}"]
        assert len(sd._talking_framings(_sc(story_talking_framings="not json"))) == 3
class TestActionMoments:
    def test_an_action_is_its_own_animated_shot_never_the_wide(self):
        """Midnight Patch's payoff (the spell, the gown, the coach) was a
        1.5 s still of the scene's wide (2026-10-14)."""
        timeline = [*_timeline()[:1], {"scene_id": "s1", "kind": "action", "speaker": None,
                                       "text": "Light sweeps over Dex and his coat becomes a gown",
                                       "characters": ["dex"], "start_s": 6.0, "end_s": 9.0},
                    _line("s1", "nova", 9.0, 12.0)]
        plan = sd.assign_sources(sd.plan_shots(SCREENPLAY, timeline, 12.0, _sc()), _sc())
        action = next(s for s in plan if s["beat_kind"] == "action")
        assert action["kind"] == "still" and action["source"] == "generative"
        assert action["characters"] == ["dex"] and action["length"] == 3.0

    def test_an_action_gets_three_seconds_on_the_track(self):
        from poindexter.services.settings_defaults import DEFAULTS

        assert float(DEFAULTS["story_action_beat_seconds"]) == 3.0


class TestTalkingFramePlace:
    """"The Signal Glitch" storyboard (2026-10-15): talking frames on a street
    instead of the workshop, strangers beside the speaker, Bolt's body guessed."""

    def _listing(self, **extra):
        sc = _sc(story_establishing_seconds="0", **extra)
        timeline = [_line("s1", "dex", 0.0, 4.0), _line("s1", "dex", 4.0, 8.0)]
        screenplay = {**SCREENPLAY, "locations": [{"id": "hub", "description": "a workshop on a skyscraper ledge"}],
                      "scenes": [{**SCREENPLAY["scenes"][0], "location": "hub"}]}
        return sd.build_shot_list(sd.assign_sources(sd.plan_shots(screenplay, timeline, 8.0, sc), sc), {},
                                  style="ink", director_model="m", site_config=sc)

    def test_the_frame_is_told_what_the_place_looks_like(self):
        first = self._listing()["shots"][0]
        assert "in a workshop on a skyscraper ledge; a lab" in first["frame_prompt"]

    def test_a_wider_setup_draws_from_the_full_body_view(self):
        shots = self._listing()["shots"]
        assert not shots[0].get("frame_full_body") and shots[1]["frame_full_body"] is True
        assert "medium shot of Dex" in shots[1]["frame_prompt"]

    def test_plain_string_framings_still_work(self):
        assert sd._talking_setups(_sc(story_talking_framings='["profile of {name}"]')) == [("profile of {name}", False)]

    def test_the_migration_moves_exactly_the_old_defaults(self):
        import importlib
        import pathlib

        from poindexter.services.settings_defaults import DEFAULTS
        from poindexter.services.video_renderers import shot_list_renderer as slr

        path = next(pathlib.Path(slr.__file__).parents[1].joinpath("migrations").glob("*talking_frames_get_the_place*.py"))
        mod = importlib.import_module(f"poindexter.services.migrations.{path.stem}")
        news = {key: new for key, _, new in mod.ROWS}
        assert news == {"story_talking_frame_template": DEFAULTS["story_talking_frame_template"],
                        "story_talking_framings": DEFAULTS["story_talking_framings"]}
