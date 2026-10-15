"""The visual format: a story told in what the audience sees.

Matt, 2026-10-15: "I like the idea of like a visual story", with Samurai
Jack as the bar. Pinned here: a visual piece's word budget comes from its
line cap, not its length; a scene or a whole piece over the cap is sent
back; few words is never "too short"; an action can set how long it is
watched; and an action shows at most two characters.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import dialogue_audio as da
from poindexter.services import screenplay as sp
from tests.unit.services.test_screenplay import _sc


def _req(**over):
    return sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "visual",
                             "screenplay_target_seconds": 60, **over}, _sc())


def _line(speaker="dex", text="Oh no."):
    return {"kind": "line", "speaker": speaker, "text": text}


def _action(text="Dex trips", chars=("dex",), **kw):
    return {"kind": "action", "text": text, "characters": list(chars), **kw}


class TestBudget:
    def test_the_budget_comes_from_the_line_cap(self):
        assert sp.visual_line_cap(60, _sc()) == 4
        assert sp.request_budget(_req(), _sc()) == 36  # 4 lines x 9 words
        assert sp.request_budget(_req(screenplay_format="dialogue"), _sc()) == 150  # 60 s x 2.5 words/s

    def test_few_words_is_never_too_short_for_a_visual_piece(self):
        assert sp.length_problem(5, 36, _sc(), allow_short=True) is None
        assert "add lines" in sp.length_problem(5, 36, _sc())


class TestChecks:
    def test_too_many_lines_in_a_piece_is_an_editor_note(self):
        play = {**sp.normalize_outline({"title": "t", "spine": dict.fromkeys(sp.SPINE_PARTS, "x"), "scenes": [
            {"scene_id": "s1", "setting": "a lab", "summary": "a", "turn": "b", "characters": ["dex"]}]})}
        play["scenes"][0]["beats"] = sp.normalize_beats([_line() for _ in range(6)])
        assert "at most 4" in sp._visual_over_cap(play, _req(), _sc())
        assert sp._visual_over_cap(play, _req(screenplay_format="dialogue"), _sc()) is None
        play["scenes"][0]["beats"] = play["scenes"][0]["beats"][:4]
        assert sp._visual_over_cap(play, _req(), _sc()) is None

    def test_an_action_shows_at_most_two_characters(self):
        beats = sp.normalize_beats([_action(chars=("dex", "nova", "dex2"))])
        req = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova,dex2", "screenplay_format": "visual",
                                "screenplay_target_seconds": 60},
                               _sc(**{"persona.dex2.display_name": "D2", "persona.dex2.enabled": "true"}))
        assert any("at most 2" in e for e in sp.check_beats(beats, "s1", req, _sc()))
        dialogue = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova,dex2", "screenplay_format": "dialogue"},
                                    _sc(**{"persona.dex2.display_name": "D2", "persona.dex2.enabled": "true"}))
        assert not [e for e in sp.check_beats(beats, "s1", dialogue, _sc()) if "at most" in e]

    def test_an_action_can_set_how_long_it_is_watched(self):
        beats = sp.normalize_beats([_action(seconds=6), _action(seconds=40), _action(seconds="x"), _action()])
        assert [b.get("seconds") for b in beats] == [6.0, 10.0, None, None]


@pytest.mark.asyncio
async def test_the_voiced_track_holds_each_action_for_its_own_length(monkeypatch, tmp_path):
    async def normalize(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes())

    async def silence(seconds, dst):
        Path(dst).write_text(str(seconds))

    async def concat(parts, dst, work):
        Path(dst).write_text(str(sum(float(Path(p).read_text()) for p in parts)))

    async def probe(path):
        return float(Path(path).read_text())

    async def synthesize(text, persona, path, **kw):
        Path(path).write_text("1.0")
        return path

    monkeypatch.setattr(da, "_normalize", normalize)
    monkeypatch.setattr(da, "_silence", silence)
    monkeypatch.setattr(da, "_concat", concat)
    monkeypatch.setattr(da, "probe_seconds", probe)
    monkeypatch.setattr(da, "_reclaim_for_tts", AsyncMock())
    play = {"title": "t", "scenes": [{"scene_id": "s1", "characters": ["dex"], "beats": [
        {"kind": "action", "text": "a long hold", "characters": ["dex"], "seconds": 6.0},
        {"kind": "line", "speaker": "dex", "text": "Oh no."},
        {"kind": "action", "text": "a quick gag", "characters": ["dex"]},
    ]}]}
    out = await da.render_dialogue(play, site_config=_sc(story_action_beat_seconds="3"),
                                   output_path=str(tmp_path / "a.wav"), synthesize=synthesize)
    actions = [b for b in out["timeline"] if b["kind"] == "action"]
    assert [round(b["end_s"] - b["start_s"], 2) for b in actions] == [6.0, 3.0]
