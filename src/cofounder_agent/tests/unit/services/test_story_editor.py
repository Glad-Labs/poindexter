"""The story editor, and a story long enough to be one.

Matt on Midnight Patch (2026-10-14): "the script really is garbage". It was a
whole Cinderella in 40 s, written in one pass: the characters announced the
premise instead of living it. Pinned here: a "story" asked for under
``story_min_story_seconds`` is refused with the 'sketch' format suggested (a
default length is raised to the floor instead); the editor
(``story.edit_notes``) gives notes that ``revise_screenplay`` applies, round
by round, until it calls the screenplay ready or ``story_edit_rounds`` runs
out; and an editor that fails keeps the draft, recorded.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from poindexter.services import screenplay as sp
from tests.unit.services.test_screenplay import _assembled, _outline, _sc, _scene


def _story_sc(**extra):
    return _sc(story_min_story_seconds="120", **extra)


class TestLengthFloor:
    def test_a_short_story_asked_for_is_refused_with_the_sketch_format_named(self):
        with pytest.raises(sp.ScreenplayError, match="sketch"):
            sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "story",
                              "screenplay_target_seconds": 40}, _story_sc())

    def test_a_default_length_is_raised_to_the_floor(self):
        req = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "story"},
                               _story_sc())
        assert req.target_seconds == 120.0

    def test_other_formats_and_a_zero_floor_are_untouched(self):
        sketch = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "sketch",
                                   "screenplay_target_seconds": 40}, _story_sc())
        assert sketch.target_seconds == 40.0
        off = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "story",
                                "screenplay_target_seconds": 40}, _sc(story_min_story_seconds="0"))
        assert off.target_seconds == 40.0


def _draft():
    return {**_assembled(), "format": "dialogue", "cast": ["dex", "nova"], "narrator": False,
            "target_seconds": 8.0, "topic": "why VRAM matters", "writer_model": "w", "notes": []}


def _revised_reply(line="Memory bandwidth. Watch: the card chokes when the weights spill over."):
    core = sp._core(_draft())
    core["scenes"][0]["beats"][1]["text"] = line
    return json.dumps(core)


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


def _editor_sc(**extra):
    return _sc(**{"story_edit_rounds": "2", "story_length_min_ratio": "0", "story_length_max_ratio": "100", **extra})


class TestEditor:
    @pytest.mark.asyncio
    async def test_notes_are_applied_until_the_editor_calls_it_ready(self, chat):
        chat.side_effect = [
            json.dumps({"verdict": "revise", "notes": ["scene s1, Nova's line: she explains instead of showing. "
                                                       "Instead, let the card choke on screen."]}),
            _revised_reply(),
            json.dumps({"verdict": "ready", "notes": []}),
        ]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"][0]["beats"][1]["text"].startswith("Memory bandwidth. Watch:")
        assert [r.get("verdict") or len(r["notes"]) for r in out["edit_rounds"]] == [1, "ready"]
        editor_prompt = chat.await_args_list[0].args[0]
        assert "story editor" in editor_prompt and "Memory bandwidth, mostly." in editor_prompt
        revise_prompt = chat.await_args_list[1].args[0]
        assert "1. scene s1, Nova's line" in revise_prompt

    @pytest.mark.asyncio
    async def test_a_ready_draft_is_left_alone(self, chat):
        chat.side_effect = [json.dumps({"verdict": "ready", "notes": []})]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"] == _draft()["scenes"] and out["edit_rounds"] == [{"round": 1, "verdict": "ready"}]
        assert chat.await_count == 1

    @pytest.mark.asyncio
    async def test_the_rounds_are_capped(self, chat):
        note = json.dumps({"verdict": "revise", "notes": ["scene s1: sharper"]})
        chat.side_effect = [note, _revised_reply("One."), note, _revised_reply("Two.")]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert len(out["edit_rounds"]) == 2 and chat.await_count == 4
        assert out["scenes"][0]["beats"][1]["text"] == "Two."

    @pytest.mark.asyncio
    async def test_zero_rounds_is_off(self, chat):
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc(story_edit_rounds="0"))
        assert chat.await_count == 0 and "edit_rounds" not in out

    @pytest.mark.asyncio
    async def test_an_editor_that_cannot_answer_keeps_the_draft_recorded(self, chat):
        chat.side_effect = ["not json", "still not json"]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"] == _draft()["scenes"] and "error" in out["edit_rounds"][0]

    @pytest.mark.asyncio
    async def test_the_writer_hands_its_draft_to_the_editor(self, chat):
        chat.side_effect = [json.dumps(_outline()), json.dumps(_scene()), json.dumps({"verdict": "ready", "notes": []})]
        req = sp.build_request({"topic": "why VRAM matters", "screenplay_cast": "dex, nova",
                                "screenplay_format": "dialogue"}, _editor_sc(story_edit_rounds="1"))
        out = await sp.write_screenplay(req, site_config=_editor_sc(story_edit_rounds="1"))
        assert out["edit_rounds"] == [{"round": 1, "verdict": "ready"}]
