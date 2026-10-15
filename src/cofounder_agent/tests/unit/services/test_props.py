"""Props: each object the story turns on is designed once and drawn that way in every shot.

"The Battery Drop" storyboard (2026-10-15) turned on a glass tube Cinder
fills with bolts until the energy cell floats to the top. Across 19 shots
it was a knee-high tube, a giant pipe she stood inside, a drinking glass at
her lips and a manhole, because every picture described it afresh.

Pinned here: ``design_props`` asks once for a name and a look per prop
(off at ``story_max_props`` = 0, empty on a failed call); ``with_props``
adds the look to any picture that shows the prop, by its whole name or its
last word, never by a word that names a cast member (the scrap "bolts" are
not Bolt the mouse); the shot list carries the designs, so a storyboard
revision redraws a shot with the same tube as the shots around it.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from poindexter.services import screenplay_director as sd
from poindexter.services.site_config import SiteConfig

TUBE = {"name": "glass tube", "look": "a narrow knee-high glass tube, as wide as a hand"}
BOLTS = {"name": "chrome bolts", "look": "thumb-sized chrome bolts"}
CAST = {"cinder", "bolt"}


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
        "story_establishing_seconds": "0",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


SCREENPLAY = {
    "title": "The Drop", "logline": "Dex floats a cell.", "cast": ["dex", "nova"],
    "scenes": [{"scene_id": "s1", "setting": "an alley by a glass tube", "characters": ["dex", "nova"], "beats": [
        {"kind": "action", "text": "Dex drops a bolt into the tube", "characters": ["dex"], "seconds": 3},
        {"kind": "line", "speaker": "nova", "text": "It moved!", "delivery": "excited"},
    ]}],
}
TIMELINE = [
    {"scene_id": "s1", "kind": "action", "text": "Dex drops a bolt into the tube", "characters": ["dex"],
     "start_s": 0.0, "end_s": 3.0},
    {"scene_id": "s1", "kind": "line", "speaker": "nova", "text": "It moved!", "start_s": 3.0, "end_s": 4.5},
]


class TestMentions:
    def test_the_whole_name_or_its_last_word_in_any_form(self):
        assert sd._mentions("Cinder lifts the glass tube", "glass tube", CAST)
        assert sd._mentions("the tube's mouth glints", "glass tube", CAST)
        assert sd._mentions("two tubes on a shelf", "glass tube", CAST)
        assert not sd._mentions("a youtube screen", "glass tube", CAST)
        assert not sd._mentions("a wide street at night", "glass tube", CAST)

    def test_a_word_that_names_the_cast_matches_only_the_whole_name(self):
        assert not sd._mentions("Bolt dances beside her", "chrome bolts", CAST)
        assert not sd._mentions("she drops three bolts", "chrome bolts", CAST)
        assert sd._mentions("a fistful of chrome bolts", "chrome bolts", CAST)


class TestWithProps:
    def test_the_look_rides_on_a_picture_that_shows_the_prop(self):
        out = sd.with_props("Close-up of Cinder's hand at the tube's mouth.", [TUBE, BOLTS], CAST)
        assert out == f"Close-up of Cinder's hand at the tube's mouth. The glass tube: {TUBE['look']}"

    def test_a_picture_without_the_prop_or_already_carrying_its_look_is_unchanged(self):
        assert sd.with_props("Bolt dances in the rain", [TUBE, BOLTS], CAST) == "Bolt dances in the rain"
        already = f"the tube, {TUBE['look']}"
        assert sd.with_props(already, [TUBE], CAST) == already


class TestDesign:
    @pytest.fixture
    def chat(self, monkeypatch):
        import poindexter.services.llm_text as llm_text

        mock = AsyncMock()
        monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
        monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "ollama/writer")
        return mock

    @pytest.mark.asyncio
    async def test_one_call_over_the_numbered_beats_names_and_looks_capped(self, chat):
        chat.return_value = json.dumps({"props": [
            TUBE, {"name": "Glass Tube", "look": "a repeat"}, {"name": "", "look": "nameless"},
            {"name": "cell", "look": ""}, BOLTS, {"name": "wire", "look": "a thin bent wire"},
        ]})
        props = await sd.design_props(SCREENPLAY, site_config=_sc(story_max_props="2"))
        assert props == [TUBE, BOLTS]
        prompt = chat.await_args.args[0]
        assert "1. (Dex drops a bolt into the tube)" in prompt and "At most 2." in prompt
        assert chat.await_args.kwargs["phase"] == "story_props"

    @pytest.mark.asyncio
    async def test_off_at_zero_and_empty_on_a_failed_call(self, chat):
        assert await sd.design_props(SCREENPLAY, site_config=_sc(story_max_props="0")) == []
        chat.assert_not_awaited()
        chat.side_effect = RuntimeError("ollama down")
        assert await sd.design_props(SCREENPLAY, site_config=_sc()) == []

    @pytest.mark.asyncio
    async def test_the_shot_list_carries_the_designs_and_every_picture_of_the_prop(self, chat):
        def reply(prompt, **kw):
            if kw.get("phase") == "story_props":
                return json.dumps({"props": [TUBE]})
            assert f"- glass tube: {TUBE['look']}" in prompt  # the illustrator sees the design
            return json.dumps({"shots": [{"id": 0, "prompt": "low angle on the tube as a bolt drops in"}]})

        chat.side_effect = reply
        listing = await sd.direct_screenplay(SCREENPLAY, TIMELINE, 4.5, site_config=_sc(), style="anime")
        assert listing["props"] == [TUBE]
        drop = listing["shots"][0]
        assert drop["prompt"].startswith(f"anime, low angle on the tube as a bolt drops in. The glass tube: {TUBE['look']}")
        talk = next(s for s in listing["shots"] if s["source"] == "presenter")
        assert TUBE["look"] in talk["frame_prompt"]  # the setting names the tube

    @pytest.mark.asyncio
    async def test_given_props_are_kept_not_redesigned(self, chat):
        chat.return_value = "{}"
        listing = await sd.direct_screenplay(SCREENPLAY, TIMELINE, 4.5, site_config=_sc(), style="", props=[TUBE])
        assert listing["props"] == [TUBE]
        assert all(c.kwargs.get("phase") != "story_props" for c in chat.await_args_list)


def test_pack_prompt_renders():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt("story.props", title="t", logline="l", beats="1. (x)", max_props="4")
    assert '{"props"' in text and "1. (x)" in text and "At most 4." in text


class TestVisible:
    """The Battery Drop's extreme close-up of a bolt sinking listed Cinder, who
    dropped it; her reference was drawn in and the close-up became her in the
    alley. The illustrator says who the picture shows."""

    def test_a_subset_of_the_row_or_nothing_said(self):
        assert sd._visible([], ["dex", "nova"]) == []
        assert sd._visible(["Nova"], ["dex", "nova"]) == ["nova"]
        assert sd._visible(["mara"], ["dex", "nova"]) is None
        assert sd._visible(None, ["dex"]) is None

    @pytest.mark.asyncio
    async def test_a_close_up_on_an_object_draws_nobody_in(self, monkeypatch):
        import poindexter.services.llm_text as llm_text

        async def reply(prompt, **kw):
            if kw.get("phase") == "story_props":
                return "{}"
            return json.dumps({"shots": [{"id": 0, "prompt": "extreme close-up of a bolt sinking", "visible": []}]})

        monkeypatch.setattr(llm_text, "ollama_chat_text", reply)
        monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "ollama/writer")
        listing = await sd.direct_screenplay(SCREENPLAY, TIMELINE, 4.5, site_config=_sc(), style="")
        assert listing["shots"][0]["characters"] == []


def test_two_props_join_with_one_period():
    out = sd.with_props("the tube and the cell.", [
        {"name": "tube", "look": "a waist-high tube."}, {"name": "cell", "look": "a gold cell."},
    ], set())
    assert ".." not in out and out.endswith("The cell: a gold cell.")
