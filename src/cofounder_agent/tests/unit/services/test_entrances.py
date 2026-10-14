"""Entrances and exits: a character is on screen only while they are in the scene.

Mara stood in every wide shot of Midnight Patch from the first second,
before her ring summoned her (2026-10-14): the director put a scene's whole
cast in every wide. An action beat now says who ``enters`` and who
``exits``; the script check keeps it consistent, the voiced timeline carries
it, and the director keeps a character out of the wides before they enter
and after they leave.
"""

from __future__ import annotations

from poindexter.services import screenplay as sp
from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig


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
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


def _request():
    return sp.build_request({"topic": "t", "screenplay_cast": ["dex", "nova"]}, _sc())


def _line(speaker, text="hello there"):
    return {"kind": "line", "speaker": speaker, "text": text}


def _action(text, chars, **kw):
    return {"kind": "action", "text": text, "characters": chars, **kw}


class TestBeats:
    def test_entrances_and_exits_are_kept_on_actions(self):
        beats = sp.normalize_beats([
            _action("Nova flickers in", ["nova"], enters=["Nova"]),
            _action("Dex waves", ["dex"]),
            _action("Nova fades out", ["nova"], exits="nova"),
        ])
        assert beats[0]["enters"] == ["nova"] and "exits" not in beats[0]
        assert "enters" not in beats[1] and beats[2]["exits"] == ["nova"]

    def test_a_consistent_scene_passes(self):
        beats = sp.normalize_beats([
            _line("dex"), _action("Nova flickers in", ["nova"], enters=["nova"]), _line("nova"),
            _action("Nova fades out", ["nova"], exits=["nova"]), _line("dex", "gone"),
        ])
        assert sp.check_beats(beats, "s1", _request()) == []

    def test_speaking_before_entering_or_after_leaving_is_sent_back(self):
        early = sp.normalize_beats([_line("nova"), _action("Nova flickers in", ["nova"], enters=["nova"])])
        assert any("speaks before entering" in e for e in sp.check_beats(early, "s1", _request()))
        late = sp.normalize_beats([_action("Nova fades out", ["nova"], exits=["nova"]), _line("nova")])
        assert any("speaks after leaving" in e for e in sp.check_beats(late, "s1", _request()))

    def test_entering_twice_or_an_unknown_name_is_sent_back(self):
        twice = sp.normalize_beats([
            _action("Nova arrives", ["nova"], enters=["nova"]), _action("Nova arrives again", ["nova"], enters=["nova"]),
        ])
        assert any("already in the scene" in e for e in sp.check_beats(twice, "s1", _request()))
        ghost = sp.normalize_beats([_action("A ghost arrives", [], enters=["ghost"])])
        assert any("'ghost', who is not in the cast" in e for e in sp.check_beats(ghost, "s1", _request()))


SCREENPLAY = {
    "title": "t", "cast": ["dex", "nova"],
    "scenes": [{"scene_id": "s1", "setting": "a lab", "characters": ["dex", "nova"], "beats": []}],
}


def _timeline():
    return [
        {"scene_id": "s1", "kind": "line", "speaker": "dex", "text": "a", "start_s": 0.0, "end_s": 6.0},
        {"scene_id": "s1", "kind": "action", "speaker": None, "text": "Nova flickers in",
         "characters": ["nova"], "enters": ["nova"], "start_s": 6.0, "end_s": 7.5},
        {"scene_id": "s1", "kind": "line", "speaker": "nova", "text": "b", "start_s": 7.5, "end_s": 13.5},
        {"scene_id": "s1", "kind": "action", "speaker": None, "text": "Nova fades out",
         "characters": ["nova"], "exits": ["nova"], "start_s": 13.5, "end_s": 15.0},
        {"scene_id": "s1", "kind": "line", "speaker": "dex", "text": "c", "start_s": 15.0, "end_s": 21.0},
    ]


class TestDirector:
    def test_a_character_is_in_the_wides_only_while_in_the_scene(self):
        plan = sd.plan_shots(SCREENPLAY, _timeline(), 21.0, _sc())
        on = [(round(s["start_s"], 2), s["kind"], s["characters"]) for s in plan if s["kind"] != "talk"]
        before = [c for start, _, c in on if start < 6.0]
        during = [c for start, _, c in on if 6.0 <= start < 15.0]
        after = [c for start, _, c in on if start >= 15.0]
        assert before and all("nova" not in c for c in before)
        assert any("nova" in c for c in during)
        assert after and all("nova" not in c for c in after)

    def test_the_entrance_is_its_own_shot_not_the_master(self):
        plan = sd.plan_shots(SCREENPLAY, _timeline(), 21.0, _sc())
        entrance = next(s for s in plan if s["start_s"] == 6.0)
        assert entrance["kind"] == "still" and entrance["beat_text"] == "Nova flickers in"
        assert entrance["characters"] == ["nova"]

    def test_a_timeline_without_entrances_keeps_the_whole_cast(self):
        timeline = [{k: v for k, v in seg.items() if k not in ("enters", "exits")} for seg in _timeline()]
        plan = sd.plan_shots(SCREENPLAY, timeline, 21.0, _sc())
        first = next(s for s in plan if s["kind"] != "talk")
        assert set(first["characters"]) == {"nova"} or set(first["characters"]) == {"dex", "nova"}
        assert first["scene_cast"] == ["dex", "nova"]
