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


def _read(*lost):
    return json.dumps({"story": "Dex asks why his card is slow; Nova shows him.", "wants": {"Dex": "a fast card"},
                       "cause": "Nova's demonstration", "lost": list(lost)})


READY = json.dumps({"verdict": "ready", "notes": []})


class TestEditor:
    @pytest.mark.asyncio
    async def test_notes_are_applied_until_the_editor_calls_it_ready(self, chat):
        chat.side_effect = [
            _read(),
            json.dumps({"verdict": "revise", "notes": ["scene s1, Nova's line: she explains instead of showing. "
                                                       "Instead, let the card choke on screen."]}),
            _revised_reply(),
            _read(),
            READY,
        ]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"][0]["beats"][1]["text"].startswith("Memory bandwidth. Watch:")
        assert [r.get("verdict") or len(r["notes"]) for r in out["edit_rounds"]] == [1, "ready"]
        editor_prompt = chat.await_args_list[1].args[0]
        assert "story editor" in editor_prompt and "Memory bandwidth, mostly." in editor_prompt
        assert "Story: Dex asks why his card is slow" in editor_prompt  # the viewer's account
        assert "Want: Dex wants his card to run a big model" in editor_prompt  # the story meant
        revise_prompt = chat.await_args_list[2].args[0]
        assert "1. scene s1, Nova's line" in revise_prompt

    @pytest.mark.asyncio
    async def test_a_lost_viewer_overrules_a_ready_verdict(self, chat):
        """"The Last Bus Home" read as craft and made no sense (2026-10-14)."""
        chat.side_effect = [_read("why does wanting light the sign?"), READY, _revised_reply(), _read(), READY]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        first = out["edit_rounds"][0]
        assert first["notes"] == ["A first-time viewer could not follow this; make it clear on screen: "
                                  "why does wanting light the sign?"]
        assert out["edit_rounds"][-1]["verdict"] == "ready"

    @pytest.mark.asyncio
    async def test_the_reader_sees_only_what_the_audience_sees(self, chat):
        chat.side_effect = [_read(), READY]
        await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        reader_prompt = chat.await_args_list[0].args[0]
        assert "DEX: Hey Nova, what makes a graphics card fast" in reader_prompt
        assert "(Nova taps a glowing GPU)" in reader_prompt
        for hidden in ("Why VRAM Matters", "memory wall", "Dex asks, Nova explains"):
            assert hidden not in reader_prompt  # title, spine, summary

    @pytest.mark.asyncio
    async def test_a_ready_draft_is_left_alone(self, chat):
        chat.side_effect = [_read(), READY]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"] == _draft()["scenes"] and out["edit_rounds"][0]["verdict"] == "ready"
        assert chat.await_count == 2

    @pytest.mark.asyncio
    async def test_the_rounds_are_capped_and_the_final_script_is_read(self, chat):
        note = json.dumps({"verdict": "revise", "notes": ["scene s1: sharper"]})
        chat.side_effect = [_read(), note, _revised_reply("One."), _read(), note, _revised_reply("Two."), _read()]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert [("notes" in r, "final_cold_read" in r) for r in out["edit_rounds"]] == [
            (True, False), (True, False), (False, True)]
        assert chat.await_count == 7 and out["scenes"][0]["beats"][1]["text"] == "Two."

    @pytest.mark.asyncio
    async def test_zero_rounds_is_off(self, chat):
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc(story_edit_rounds="0"))
        assert chat.await_count == 0 and "edit_rounds" not in out

    @pytest.mark.asyncio
    async def test_a_failed_cold_read_still_gets_an_editor(self, chat):
        chat.side_effect = ["no", "still no", READY]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert "the cold read failed" in chat.await_args_list[2].args[0]
        assert out["edit_rounds"][0]["verdict"] == "ready"

    @pytest.mark.asyncio
    async def test_an_editor_that_cannot_answer_keeps_the_draft_recorded(self, chat):
        chat.side_effect = [_read(), "not json", "still not json"]
        out = await sp.edit_screenplay(_draft(), site_config=_editor_sc())
        assert out["scenes"] == _draft()["scenes"] and "error" in out["edit_rounds"][0]

    @pytest.mark.asyncio
    async def test_the_writer_hands_its_draft_to_the_editor(self, chat):
        chat.side_effect = [json.dumps(_outline()), json.dumps(_scene()), _read(), READY]
        req = sp.build_request({"topic": "why VRAM matters", "screenplay_cast": "dex, nova",
                                "screenplay_format": "dialogue"}, _editor_sc(story_edit_rounds="1"))
        out = await sp.write_screenplay(req, site_config=_editor_sc(story_edit_rounds="1"))
        assert out["edit_rounds"][0]["verdict"] == "ready"


class TestAdaptation:
    def test_a_listed_tale_is_kept_in_its_listed_spelling(self):
        req = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "dialogue",
                                "screenplay_source": "the tortoise and the HARE"}, _sc())
        assert req.source == "The Tortoise and the Hare"

    def test_an_unlisted_source_is_refused(self):
        with pytest.raises(sp.ScreenplayError, match="story_source_tales"):
            sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "dialogue",
                              "screenplay_source": "A Recent Blockbuster"}, _sc())

    def test_the_source_reaches_the_outline_and_survives_revision(self):
        block = sp._source_block(sp.ScreenplayRequest(topic="t", source="Stone Soup"))
        assert '"Stone Soup"' in block and "never a film" in block and "same ending" in block
        assert "original story" in sp._source_block(sp.ScreenplayRequest(topic="t"))
        assert sp._request_from({"topic": "t", "source": "Stone Soup"}).source == "Stone Soup"

    @pytest.mark.asyncio
    async def test_the_writer_is_told_to_adapt(self, chat):
        chat.side_effect = [json.dumps(_outline()), json.dumps(_scene())]
        req = sp.build_request({"topic": "t", "screenplay_cast": "dex,nova", "screenplay_format": "dialogue",
                                "screenplay_source": "Stone Soup"}, _sc())
        out = await sp.write_screenplay(req, site_config=_sc(story_length_min_ratio="0", story_length_max_ratio="100"))
        assert 'adapt the traditional tale "Stone Soup"' in chat.await_args_list[0].args[0]
        assert out["source"] == "Stone Soup"
