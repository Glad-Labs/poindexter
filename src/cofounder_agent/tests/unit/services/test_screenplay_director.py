"""Creative studio phase 4 (poindexter#1138): screenplay -> shot list.

The invariant everything rests on: the shots tile the measured dialogue track
exactly, so each lip-sync window lands on its line. Plus the film grammar the
renderer depends on — a talking shot never exceeds one seamless S2V chunk, one
speaker never talks three shots running, no source runs three times — and a
visuals call that fails or skips shots never blocks the render.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from poindexter.modules.content.atoms import media_direct_screenplay as atom
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.nova.portrait_url": "https://cdn/nova.png",
        "persona.pip.display_name": "Pip", "persona.pip.enabled": "true",  # no portrait
        "video_presenter_max_chunks_per_shot": "1",
        "video_comfyui_s2v_length_frames": "77",
        "video_comfyui_fps": "16",          # budget = 77/16 - 0.15 = 4.6625 s
        "video_hero_shots_max": "2",
        "story_max_shot_seconds": "6",
        "story_min_shot_seconds": "0.8",
        "story_min_animated_shot_seconds": "2.5",
        # Pacing tests below are about lines and cutaways; the establishing
        # wide has its own tests (test_wides.py).
        "story_establishing_seconds": "0",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


SCREENPLAY = {
    "title": "Why VRAM Matters", "logline": "Dex learns.", "cast": ["dex", "nova", "pip"],
    "scenes": [
        {"scene_id": "s1", "setting": "a cosy lab at night", "characters": ["dex", "nova"], "beats": [
            {"kind": "line", "speaker": "narrator", "text": "In the lab.", "delivery": ""},
            {"kind": "line", "speaker": "dex", "text": "Short line.", "delivery": "excited"},
            {"kind": "line", "speaker": "nova", "text": "A long explanation.", "delivery": "patient"},
            {"kind": "action", "text": "Nova taps the GPU", "characters": ["nova"]},
            {"kind": "line", "speaker": "pip", "text": "Pip has no portrait.", "delivery": ""},
        ]},
    ],
}


def _line(i, speaker, text, start, end):
    return {"beat_idx": i, "scene_id": "s1", "kind": "line", "speaker": speaker, "text": text,
            "characters": ["dex", "nova"], "start_s": start, "end_s": end}


TIMELINE = [
    _line(0, "narrator", "In the lab.", 0.0, 3.0),
    _line(1, "dex", "Short line.", 3.35, 6.0),
    _line(2, "nova", "A long explanation.", 6.35, 16.0),          # 9.65 s + gap: talk + cutaways
    {"beat_idx": 3, "scene_id": "s1", "kind": "action", "speaker": None, "text": "Nova taps the GPU",
     "characters": ["nova"], "start_s": 16.0, "end_s": 17.5},
    _line(4, "pip", "Pip has no portrait.", 17.85, 20.0),
]
TRACK = 20.4


def _plan(sc=None, timeline=TIMELINE, screenplay=SCREENPLAY, track=TRACK):
    sc = sc or _sc()
    return sd.assign_sources(sd.plan_shots(screenplay, timeline, track, sc), sc)


class TestPacing:
    """A line a little longer than one talking shot used to leave a 0.1 s
    cutaway flash (Midnight Patch rough cut, 2026-10-14)."""

    def _talk(self, end, sc=None):
        timeline = [_line(0, "dex", "A line.", 0.0, end)]
        screenplay = {**SCREENPLAY, "scenes": [{**SCREENPLAY["scenes"][0], "beats": [
            {"kind": "line", "speaker": "dex", "text": "A line.", "delivery": ""}]}]}
        return [(s["kind"], round(s["length"], 2)) for s in _plan(sc, timeline, screenplay, end)]

    def test_a_short_remainder_becomes_a_real_cutaway(self):
        # 4.8 s line, 4.66 s talking budget: 0.14 s left over.
        plan = self._talk(4.8)
        assert plan[0] == ("talk", 3.0) and all(length >= 1.8 for _, length in plan[1:])
        assert sum(length for _, length in plan) == 4.8

    def test_a_line_that_fits_is_one_shot(self):
        assert self._talk(4.0) == [("talk", 4.0)]

    def test_the_minimum_is_a_setting(self):
        plan = self._talk(4.8, _sc(story_min_reaction_seconds="0"))
        assert plan[0] == ("talk", 4.66)


class TestPlacePlate:
    def test_each_scene_gets_an_empty_plate_on_its_establishing_shot(self):
        shots = [{"source": "image_gen", "scene_id": "s1"}, {"source": "presenter", "scene_id": "s1"}]
        plan = [{"kind": "master", "setting": "a rooftop dock"}, {"kind": "talk", "setting": "a rooftop dock"}]
        sd._mark_establishing(shots, plan, lambda setting: sd._place_prompt(setting, "ink", _sc()))
        assert shots[0]["place_prompt"] == "ink, the empty place with nobody in it: a rooftop dock, wide view"
        assert "place_prompt" not in shots[1]


class TestTiling:
    def test_shots_tile_the_track_with_no_gap_or_overlap(self):
        plan = _plan()
        t = 0.0
        for shot in plan:
            assert abs(shot["start_s"] - t) < 1e-6
            t += shot["length"]
        assert abs(t - TRACK) < 1e-6

    def test_a_talking_shot_never_exceeds_one_chunk(self):
        budget = slr.presenter_window_budget_s(_sc())
        assert all(s["length"] <= budget + 1e-6 for s in _plan() if s["source"] == "presenter")

    def test_a_long_line_keeps_its_start_on_the_face_and_cuts_to_the_listener(self):
        """The rest of a long line is a reaction shot: the scene's other
        characters listening, not a picture of what is being said."""
        nova = [s for s in _plan() if s["beat_text"] == "A long explanation."]
        assert nova[0]["source"] == "presenter" and nova[0]["speaker"] == "nova"
        assert all(s["source"] != "presenter" and s["characters"] == ["dex"] for s in nova[1:])
        assert all(s["kind"] == "reaction" for s in nova[1:])
        assert all(s["length"] <= 6.0 + 1e-6 for s in nova[1:])

    def test_who_is_in_each_still(self):
        plan = _plan()
        by_text = {s["beat_text"]: s for s in plan}
        assert by_text["In the lab."]["characters"] == ["dex", "nova"]  # narrator: the scene's cast
        # A 1.5 s action beat is too short to establish anything: it is the
        # scene's master shot, which shows the scene's whole cast.
        assert by_text["Nova taps the GPU"]["kind"] == "master"
        assert by_text["Nova taps the GPU"]["characters"] == ["dex", "nova"]
        # Pip has no portrait, so Pip cannot be drawn: the line plays over the
        # scene's drawable cast. Putting Pip in the picture sent the still to
        # the text path and, when that failed, to stock footage (2026-10-14).
        pip = by_text["Pip has no portrait."]
        assert pip["source"] != "presenter" and "pip" not in pip["characters"]
        assert all("pip" not in s["characters"] for s in plan)

    def test_delivery_note_and_setting_ride_on_the_talking_shot(self):
        dex = next(s for s in _plan() if s.get("speaker") == "dex")
        assert dex["delivery"] == "excited" and dex["setting"] == "a cosy lab at night"


class TestSlivers:
    def test_a_sliver_folds_into_its_still_neighbour(self):
        timeline = [
            _line(0, "narrator", "In the lab.", 0.0, 3.0),
            {"beat_idx": 1, "scene_id": "s1", "kind": "action", "speaker": None, "text": "blink",
             "characters": [], "start_s": 3.0, "end_s": 3.4},
            _line(2, "dex", "Short line.", 3.4, 6.0),
        ]
        plan = sd.plan_shots(SCREENPLAY, timeline, 6.0, _sc())
        assert [round(s["length"], 2) for s in plan] == [3.4, 2.6]

    def test_a_sliver_between_two_talking_shots_stays_its_own_shot(self):
        timeline = [
            _line(0, "dex", "Short line.", 0.0, 3.0),
            {"beat_idx": 1, "scene_id": "s1", "kind": "action", "speaker": None, "text": "blink",
             "characters": [], "start_s": 3.0, "end_s": 3.3},
            _line(2, "nova", "A long explanation.", 3.3, 6.0),
        ]
        plan = sd.plan_shots(SCREENPLAY, timeline, 6.0, _sc())
        assert [s["kind"] for s in plan] == ["talk", "master", "talk"]


class TestSources:
    def test_a_third_talking_shot_in_a_row_by_one_speaker_becomes_a_cutaway(self):
        timeline = [_line(i, "dex", "Short line.", i * 3.0, i * 3.0 + 3.0) for i in range(3)]
        plan = _plan(timeline=timeline, track=9.0)
        assert [s["source"] for s in plan][:2] == ["presenter", "presenter"]
        # The third becomes a reaction: whoever listens to dex in the scene.
        assert plan[2]["source"] != "presenter" and plan[2]["characters"] == ["nova"]

    def test_a_cartoon_animates_every_non_talking_shot_but_its_wides(self):
        """Matt, 2026-10-14: a cartoon is animated "start to finish", not still
        pans; a wide is the exception, a painting the camera moves across
        (story_wide_motion), unless that is set to "animate"."""
        plan = _plan(sc=_sc(story_min_animated_shot_seconds="0"))
        not_talking = [s for s in plan if s["source"] != "presenter"]
        assert not_talking
        assert all(s["source"] == "generative" for s in not_talking if s["kind"] != "master")
        assert all(s["source"] == "image_kenburns" for s in not_talking if s["kind"] == "master")
        animated = _plan(sc=_sc(story_min_animated_shot_seconds="0", story_wide_motion="animate"))
        assert all(s["source"] == "generative" for s in animated if s["source"] != "presenter")

    def test_the_cartoon_animation_budget_is_its_own_setting(self):
        plan = _plan(sc=_sc(story_max_animated_shots="1", story_min_animated_shot_seconds="2.5"))
        eligible = sorted((s["length"] for s in plan if s["kind"] != "talk" and s["length"] >= 2.5), reverse=True)
        animated = sorted((s["length"] for s in plan if s["source"] == "generative"), reverse=True)
        assert animated == eligible[:1]  # longest first; video_hero_shots_max (2 here) is the blog's

    def test_talking_shot_cap(self):
        plan = _plan(sc=_sc(story_max_talking_shots="1"))
        assert sum(1 for s in plan if s["source"] == "presenter") == 1


class TestBuild:
    def test_carries_per_piece_portraits(self):
        plain = sd.build_shot_list(_plan(), {}, style="ink", director_model="m")
        assert plain["cast_portraits"] == {}
        styled = sd.build_shot_list(_plan(), {}, style="ink", director_model="m",
                                    cast_portraits={"dex": "https://cdn/dex-ink.png"})
        assert styled["cast_portraits"] == {"dex": "https://cdn/dex-ink.png"}

    def test_validates_and_carries_cast_fields(self):
        plan = _plan()
        listing = sd.build_shot_list(plan, {}, style="flat 2D cartoon", director_model="m")
        assert abs(listing["total_duration_s"] - TRACK) < 0.01
        talk = next(s for s in listing["shots"] if s["source"] == "presenter")
        assert talk["speaker"] in ("dex", "nova") and talk["characters"] == [talk["speaker"]]
        assert "a cosy lab at night" in talk["prompt"]
        still = next(s for s in listing["shots"] if s["source"] != "presenter")
        assert still["prompt"].startswith("flat 2D cartoon, ")
        assert listing["director_prompt_version"] == sd.DIRECTOR_PROMPT_VERSION
        assert listing["timing"] == "exact"

    def test_intents_describe_pictures_not_dialogue(self):
        """Vision QA grades the frame against ``intent``; a spoken line there
        made a correct talking frame fail and fall back to a held frame."""
        plan = _plan()
        listing = sd.build_shot_list(plan, {}, style="anime", director_model="m")
        talk = next(s for s in listing["shots"] if s["source"] == "presenter")
        assert talk["intent"] == f"{talk['speaker']} talking in a cosy lab at night"
        with_looks = sd.build_shot_list(plan, {}, style="anime", director_model="m",
                                        site_config=_sc(**{"persona.dex.appearance": "a fox in goggles",
                                                           "persona.nova.appearance": "a robot owl"}))
        talk2 = next(s for s in with_looks["shots"] if s["source"] == "presenter")
        assert talk2["intent"].startswith(("Dex, a fox in goggles, talking", "Nova, a robot owl, talking"))
        for shot, planned in zip(listing["shots"], plan, strict=True):
            if shot["source"] == "presenter":
                continue
            if planned["kind"] == "master":
                assert shot["intent"].lower().startswith("wide shot of dex and nova in a cosy lab at night")
            else:
                assert shot["intent"].startswith("a cosy lab at night")

    def test_llm_visuals_replace_the_fallback(self):
        plan = _plan()
        i = next(k for k, s in enumerate(plan) if s["source"] == "generative")
        listing = sd.build_shot_list(
            plan, {i: {"prompt": "wide shot of the lab", "motion": "slow pan"}},
            style="anime", director_model="m",
        )
        assert listing["shots"][i]["prompt"] == "anime, wide shot of the lab"
        assert listing["shots"][i]["motion"] == "slow pan"


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "ollama/writer")
    return mock


class TestVisuals:
    @pytest.mark.asyncio
    async def test_maps_ids_and_ignores_talking_and_unknown_shots(self, chat):
        plan = _plan()
        talk = next(i for i, s in enumerate(plan) if s["source"] == "presenter")
        still = next(i for i, s in enumerate(plan) if s["source"] != "presenter")
        chat.return_value = json.dumps({"shots": [
            {"id": still, "prompt": "p", "motion": "m"},
            {"id": talk, "prompt": "should be ignored"},
            {"id": 999, "prompt": "unknown"},
        ]})
        out, model = await sd.write_visuals(plan, SCREENPLAY, site_config=_sc())
        assert out == {still: {"prompt": "p", "motion": "m"}} and model == "ollama/writer"
        assert chat.await_args.kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_a_failed_call_returns_nothing_and_never_raises(self, chat):
        chat.side_effect = RuntimeError("ollama down")
        out, _ = await sd.write_visuals(_plan(), SCREENPLAY, site_config=_sc())
        assert out == {}


def test_pack_prompt_renders():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt("story.shot_visuals", title="t", logline="l", shot_rows="- id 1: x")
    assert '{"shots"' in text and "- id 1: x" in text


class TestAtom:
    def test_contract(self):
        assert atom.ATOM_META.produces == ("video_shot_list",)
        assert set(atom.ATOM_META.requires) == {"screenplay", "dialogue_timeline", "long_narration_audio_path"}

    @pytest.mark.asyncio
    async def test_style_from_config_else_house_style(self, monkeypatch):
        seen = {}

        async def fake(screenplay, timeline, track_seconds, **kw):
            seen.update(kw, track=track_seconds)
            return {"shots": []}

        import poindexter.services.dialogue_audio as da
        monkeypatch.setattr(sd, "direct_screenplay", fake)
        monkeypatch.setattr(da, "probe_seconds", AsyncMock(return_value=20.4))
        state = {"screenplay": SCREENPLAY, "dialogue_timeline": TIMELINE,
                 "long_narration_audio_path": "/x.wav", "site_config": _sc(media_house_style="risograph print")}
        await atom.run({**state, "screenplay_visual_style": "anime"})
        assert seen["style"] == "anime" and seen["track"] == 20.4
        await atom.run(state)
        assert seen["style"] == "risograph print"

    @pytest.mark.asyncio
    async def test_missing_inputs_name_the_fix(self):
        with pytest.raises(RuntimeError, match="render_dialogue first"):
            await atom.run({"site_config": _sc(), "screenplay": SCREENPLAY})


class TestCastRenderPrompts:
    def test_cast_shot_acts_the_line_and_presenter_keeps_its_template(self):
        from poindexter.schemas.video_shot_list import Shot
        from poindexter.services.persona_service import get_persona

        sc = _sc()
        cast = Shot(idx=0, duration_s=4, intent="x", source="presenter", narration_offset_s=0,
                    speaker="dex", prompt="excited; a cosy lab")
        plain = Shot(idx=0, duration_s=4, intent="x", source="presenter", narration_offset_s=0)
        dex = get_persona(sc, "dex")
        assert slr._compose_presenter_prompt(cast, dex, sc).startswith("Dex talks in the scene")
        assert "excited; a cosy lab" in slr._compose_presenter_prompt(cast, dex, sc)
        assert "in a studio" in slr._compose_presenter_prompt(plain, dex, sc)
        sc2 = _sc(video_cast_negative_prompt="blurry", video_presenter_negative_prompt="bobbing")
        assert slr._presenter_negative_prompt(sc2, cast=True) == {"negative_prompt": "blurry"}
        assert slr._presenter_negative_prompt(sc2) == {"negative_prompt": "bobbing"}


class TestCoverage:
    """Coverage, not illustration (2026-10-13): the first cartoon cut talk /
    1.5 s new picture / talk, and its stills illustrated the dialogue."""

    def test_every_master_shot_of_a_scene_is_the_same_picture(self):
        timeline = [
            _line(0, "dex", "Short line.", 0.0, 3.0),
            {"beat_idx": 1, "scene_id": "s1", "kind": "action", "speaker": None, "text": "dex shrugs",
             "characters": ["dex"], "start_s": 3.0, "end_s": 4.5},
            _line(2, "nova", "A long explanation.", 4.5, 7.5),
            {"beat_idx": 3, "scene_id": "s1", "kind": "action", "speaker": None, "text": "nova taps",
             "characters": ["nova"], "start_s": 7.5, "end_s": 9.0},
            _line(4, "dex", "Short line.", 9.0, 12.0),
        ]
        plan = _plan(timeline=timeline, track=12.0)
        listing = sd.build_shot_list(plan, {}, style="anime", director_model="m", site_config=_sc())
        masters = [s for s, p in zip(listing["shots"], plan, strict=True) if p["kind"] == "master"]
        assert len(masters) == 2
        assert masters[0]["prompt"] == masters[1]["prompt"]
        assert masters[0]["prompt"].startswith("anime, wide shot of Dex and Nova in a cosy lab at night")
        assert all(s["source"] != "generative" for s in masters)

    @pytest.mark.asyncio
    async def test_masters_never_reach_the_illustrator_and_reactions_name_the_speaker(self, monkeypatch):
        seen = {}

        async def fake_chat(prompt, **kw):
            seen["prompt"] = prompt
            return "{}"

        monkeypatch.setattr("poindexter.services.llm_text.ollama_chat_text", fake_chat)
        monkeypatch.setattr("poindexter.services.llm_text.resolve_local_writer_model",
                            lambda site_config=None: "m")
        plan = _plan()
        await sd.write_visuals(plan, SCREENPLAY, site_config=_sc())
        rows = [line for line in seen["prompt"].splitlines() if line.startswith("- id ")]
        master_ids = {i for i, s in enumerate(plan) if s["kind"] == "master"}
        assert master_ids and not any(r.startswith(f"- id {i}:") for r in rows for i in master_ids)
        reactions = [r for r in rows if "reaction while" in r]
        assert reactions and all('nova says: "A long explanation."' in r for r in reactions)
        assert all("show: dex listening" in r for r in reactions)
