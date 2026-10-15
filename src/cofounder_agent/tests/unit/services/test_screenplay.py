"""Creative studio phase 2 (poindexter#1136): the screenplay writer.

Pins the contract the dialogue and director steps rely on: requests are
validated before any LLM call, the cast is personas, a reply is normalized and
checked (cast-only speakers, settings, length), a bad reply gets exactly one
FIX repair, and a second failure raises instead of passing junk downstream.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from poindexter.modules.content.atoms import story_write_screenplay as atom
from poindexter.services import screenplay as sp
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex",
        "persona.dex.personality": "eager, punny",
        "persona.dex.appearance": "a young fox engineer in goggles",
        "persona.dex.enabled": "true",
        "persona.nova.display_name": "Nova",
        "persona.nova.personality": "calm, precise",
        "persona.nova.enabled": "true",
        "persona.off.display_name": "Off",
        "persona.off.enabled": "false",
        "story_speech_words_per_second": "2.5",
        "story_default_target_seconds": "8",  # 20-word budget keeps fixtures small
        "site_name": "Glad Labs",
        # The writer's own tests: the story editor and the story length floor
        # have theirs (test_story_editor.py).
        "story_edit_rounds": "0",
        "story_min_story_seconds": "0",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


LINES = (("dex", "Hey Nova, what makes a graphics card fast for AI work?"),
         ("nova", "Memory bandwidth, mostly. The model has to stream every weight."))


def _outline(n=1, chars=("Dex", "nova")):
    return {
        "title": "Why VRAM Matters",
        "logline": "Dex learns why memory bandwidth rules.",
        "spine": {"want": "Dex wants his card to run a big model", "obstacle": "he thinks speed is everything",
                  "turn": "Nova shows him the memory wall", "payoff": "Dex picks memory over clock speed"},
        "scenes": [
            {"scene_id": f"s{i}", "setting": "a cosy lab at night", "summary": "Dex asks, Nova explains",
             "turn": "Dex sees the memory is the limit", "characters": list(chars)}
            for i in range(1, n + 1)
        ],
    }


def _scene(lines=LINES):
    return {"beats": [
        *({"kind": "line", "speaker": sp_, "text": t, "delivery": "curious"} for sp_, t in lines),
        {"kind": "action", "text": "Nova taps a glowing GPU", "characters": ["nova"]},
    ]}


def _assembled(lines=LINES):
    o = sp.normalize_outline(_outline())
    o["scenes"][0]["beats"] = sp.normalize_beats(_scene(lines)["beats"])
    return o


def _req(**over):
    base = {"topic": "why VRAM matters", "screenplay_cast": "dex, nova", "screenplay_format": "dialogue"}
    base.update(over)
    return sp.build_request(base, _sc())


# --- request ----------------------------------------------------------------


class TestBuildRequest:
    def test_cast_csv_and_defaults(self):
        req = _req()
        assert req.cast == ("dex", "nova") and req.format == "dialogue"
        assert req.target_seconds == 8.0 and req.narrator is True

    def test_narrator_can_name_its_persona(self):
        assert _req(screenplay_narrator="Nova").narrator_persona == "nova"
        assert _req(screenplay_narrator="").narrator_persona == ""
        assert _req(screenplay_narrator="narrator").narrator_persona == ""
        off = _req(screenplay_narrator="none")
        assert off.narrator is False and off.narrator_persona == ""

    def test_list_cast_and_narrator_off(self):
        req = _req(screenplay_cast=["Dex", "nova", "dex"], screenplay_narrator="none")
        assert req.cast == ("dex", "nova") and req.narrator is False

    @pytest.mark.parametrize("over, msg", [
        ({"topic": ""}, "topic"),
        ({"screenplay_format": "opera"}, "explainer"),
        ({"screenplay_cast": "dex"}, "two cast members"),
        ({"screenplay_format": "story", "screenplay_cast": "", "screenplay_narrator": "none"}, "nobody can speak"),
        ({"screenplay_target_seconds": "soon"}, "number"),
        ({"screenplay_target_seconds": -5}, "positive"),
    ])
    def test_rejections_name_the_fix(self, over, msg):
        with pytest.raises(sp.ScreenplayError, match=msg):
            _req(**over)

    def test_default_cast_setting_fills_the_gap(self):
        sc = _sc(story_default_cast="nova")
        req = sp.build_request({"topic": "t", "screenplay_format": "explainer"}, sc)
        assert req.cast == ("nova",)


def test_scene_count_follows_length():
    assert sp.scene_count(120, _sc()) == 3
    assert sp.scene_count(10, _sc()) == 1
    assert sp.scene_count(120, _sc(story_seconds_per_scene="60")) == 2


class TestCast:
    def test_unknown_and_disabled_personas_raise_with_the_fix(self):
        with pytest.raises(sp.ScreenplayError, match="personas create ghost"):
            sp.resolve_cast(_sc(), ("ghost",))
        with pytest.raises(sp.ScreenplayError, match="off"):
            sp.resolve_cast(_sc(), ("off",))

    def test_cast_block_leads_with_the_slug(self):
        block = sp.cast_block(sp.resolve_cast(_sc(), ("dex",)))
        assert block == "- dex: Dex; personality: eager, punny; looks: a young fox engineer in goggles"


# --- normalize + validate -----------------------------------------------------


class TestValidate:
    def test_valid_screenplay_passes(self):
        assert sp.validate(_assembled(), _req(), _sc()) == []

    def test_normalize_lowercases_ids_and_drops_empty_beats(self):
        beats = sp.normalize_beats([*_scene()["beats"], {"kind": "line", "speaker": "DEX", "text": "  "}])
        assert len(beats) == 3 and beats[0]["speaker"] == "dex"
        assert sp.normalize_outline(_outline())["scenes"][0]["characters"] == ["dex", "nova"]

    def test_unknown_speaker_is_a_hard_fix(self):
        bad = (("zed", "I am not in the cast."), LINES[1])
        errors = sp.validate(_assembled(bad), _req(), _sc())
        assert any("'zed' cannot speak" in e for e in errors)

    def test_narrator_only_when_allowed(self):
        lines = (("narrator", "Deep in the lab, two friends wonder about memory."), LINES[0])
        assert sp.validate(_assembled(lines), _req(), _sc()) == []
        errors = sp.validate(_assembled(lines), _req(screenplay_narrator="none"), _sc())
        assert any("'narrator' cannot speak" in e for e in errors)

    def test_outline_problems(self):
        o = sp.normalize_outline({"title": "", "scenes": [{"scene_id": "s1", "characters": ["zed"]}]})
        errors = sp.check_outline(o, _req())
        assert any("title" in e for e in errors) and any("setting" in e for e in errors)
        assert any("summary" in e for e in errors) and any("'zed' is not in the cast" in e for e in errors)

    def test_length_window_is_its_own_soft_check(self):
        assert sp.length_problem(20, 20, _sc()) is None
        assert "add lines" in sp.length_problem(5, 20, _sc())
        assert "cut lines" in sp.length_problem(60, 20, _sc())


# --- the real pack prompts ------------------------------------------------------


def test_pack_prompts_render_with_the_variables_code_passes():
    from poindexter.services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    common = dict(site_name="Glad Labs", topic="t", brief="b", format_name="dialogue", tone="playful",
                  word_budget=200, target_seconds=80, cast_block="- dex: Dex", narrator_available="yes",
                  source_block=sp._source_block(sp.ScreenplayRequest(topic="t", source="The Tortoise and the Hare")))
    outline = pm.get_prompt("story.outline", scene_count=2, **common)
    assert '{"title"' in outline and "- dex: Dex" in outline and "about 2 scene" in outline
    assert 'adapt the traditional tale "The Tortoise and the Hare"' in outline
    editor = pm.get_prompt("story.edit_notes", site_name="Glad Labs", format_name="story", target_seconds=120,
                           cast_block="- dex: Dex", current="{}", spine_block="Want: w",
                           source_block="SOURCE: none.", cold_read="Story: s", continuity="- beat 3: x",
                           max_notes=6)
    assert "Story: s" in editor and "Want: w" in editor and "at most 6 notes" in editor and "beat 3: x" in editor
    checker = pm.get_prompt("story.continuity", numbered_view="  1. DEX: hi")
    assert "1. DEX: hi" in checker and '"contradictions"' in checker
    assert "TRANSLATE AN ADAPTED TALE" in outline and '"main_event"' in outline
    reader = pm.get_prompt("story.cold_read", audience_view="  DEX: hi")
    assert "DEX: hi" in reader and '"lost"' in reader
    scene = pm.get_prompt(
        "story.scene", outline_block="- s1: x", spine_block="Want: w", scene_id="s1",
        scene_setting="lab", scene_summary="Dex asks", scene_turn="Dex gets it",
        scene_position="This is the LAST scene.", scene_characters="dex", scene_word_budget=60,
        previous_lines="(this is the opening scene)", **common,
    )
    assert '{"beats"' in scene and "about 60 spoken words" in scene
    assert "Want: w" in scene and "By the end of the scene: Dex gets it" in scene
    assert '"intensity"' in scene and '"spine"' in outline
    fix = pm.get_prompt("story.screenplay_fix", previous='{"title": "x"}', errors="FIX: a")
    assert '{"title": "x"}' in fix and "FIX: a" in fix


# --- write loop --------------------------------------------------------------------


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


class TestWriteScreenplay:
    @pytest.mark.asyncio
    async def test_outline_then_one_call_per_scene(self, chat):
        chat.side_effect = [json.dumps(_outline(n=2)), json.dumps(_scene()), json.dumps(_scene())]
        # Length is not under test here: open the window so no soft retry fires.
        out = await sp.write_screenplay(
            _req(), site_config=_sc(story_length_min_ratio="0", story_length_max_ratio="100"))
        assert [s["scene_id"] for s in out["scenes"]] == ["s1", "s2"]
        assert out["cast"] == ["dex", "nova"] and out["writer_model"] == "ollama/test-writer"
        assert out["notes"] == [] and chat.await_count == 3
        assert all(c.kwargs["think"] is False and c.kwargs["response_format"] == {"type": "json_object"}
                   for c in chat.await_args_list)
        second_scene_prompt = chat.await_args_list[2].args[0]
        assert "Hey Nova, what makes a graphics card fast" in second_scene_prompt  # continuity

    @pytest.mark.asyncio
    async def test_hard_problem_gets_one_repair(self, chat):
        bad = _scene((("zed", "I am not in the cast at all."), LINES[1]))
        chat.side_effect = [json.dumps(_outline()), json.dumps(bad), json.dumps(_scene())]
        out = await sp.write_screenplay(_req(), site_config=_sc(story_length_min_ratio="0"))
        assert out["scenes"][0]["beats"][0]["speaker"] == "dex"
        repair = chat.await_args_list[2].args[0]
        assert "'zed' cannot speak" in repair and "YOUR PREVIOUS REPLY" in repair

    @pytest.mark.asyncio
    async def test_hard_problem_twice_raises(self, chat):
        bad = _scene((("zed", "nope"), ("zed", "still nope")))
        chat.side_effect = [json.dumps(_outline()), json.dumps(bad), json.dumps(bad)]
        with pytest.raises(sp.ScreenplayError, match="'zed' cannot speak"):
            await sp.write_screenplay(_req(), site_config=_sc())

    @pytest.mark.asyncio
    async def test_short_scene_retries_then_is_accepted_with_a_note(self, chat):
        short = _scene((("dex", "Hi."), ("nova", "Hello.")))
        chat.side_effect = [json.dumps(_outline()), json.dumps(short), json.dumps(short)]
        out = await sp.write_screenplay(_req(), site_config=_sc())
        assert chat.await_count == 3
        assert any("add lines" in n for n in out["notes"])

    @pytest.mark.asyncio
    async def test_unparseable_outline_twice_raises(self, chat):
        chat.side_effect = ["not json", "still not json"]
        with pytest.raises(sp.ScreenplayError, match="JSON object"):
            await sp.write_screenplay(_req(), site_config=_sc())
        assert chat.await_count == 2


class TestAtom:
    def test_contract(self):
        assert atom.ATOM_META.name == "story.write_screenplay"
        assert atom.ATOM_META.requires == ("topic",) and atom.ATOM_META.produces == ("screenplay",)

    @pytest.mark.asyncio
    async def test_node_config_reaches_the_writer(self, chat):
        chat.side_effect = [json.dumps(_outline()), json.dumps(_scene())]
        out = await atom.run({
            "topic": "why VRAM matters", "site_config": _sc(),
            "screenplay_cast": ["dex", "nova"], "screenplay_format": "dialogue",
        })
        assert out["screenplay"]["format"] == "dialogue"

    @pytest.mark.asyncio
    async def test_bad_request_raises_before_any_llm_call(self, chat):
        with pytest.raises(sp.ScreenplayError):
            await atom.run({"topic": "t", "site_config": _sc(), "screenplay_format": "opera"})
        chat.assert_not_awaited()



# --- story spine + line intensity (2026-10-13) -------------------------------


class TestSpine:
    def test_an_outline_without_a_spine_or_scene_turns_is_sent_back(self):
        o = _outline()
        del o["spine"]
        del o["scenes"][0]["turn"]
        errors = sp.check_outline(sp.normalize_outline(o), _req())
        assert any('"spine"' in e for e in errors)
        assert any('"turn"' in e for e in errors)

    def test_a_full_spine_passes(self):
        assert sp.check_outline(sp.normalize_outline(_outline()), _req()) == []

    def test_intensity_is_kept_and_defaults_to_medium(self):
        beats = sp.normalize_beats([
            {"kind": "line", "speaker": "dex", "text": "What?!", "intensity": "HIGH"},
            {"kind": "line", "speaker": "nova", "text": "Calm down.", "intensity": "seething"},
            {"kind": "line", "speaker": "dex", "text": "Fine."},
        ])
        assert [b["intensity"] for b in beats] == ["high", "medium", "medium"]

    def test_the_last_scene_is_told_to_pay_off_the_opening(self):
        outline = sp.normalize_outline(_outline(n=2))
        middle = sp._scene_position(outline, 0, [])
        last = sp._scene_position(outline, 1, ["dex: Why is my card crashing?"])
        assert middle.startswith("The next scene:")
        assert "LAST scene" in last and "Dex picks memory over clock speed" in last
        assert "Why is my card crashing?" in last


# --- revision (chat drafting) ---------------------------------------------------


def _written():
    out = _assembled()
    out.update({"format": "dialogue", "cast": ["dex", "nova"], "narrator": True, "narrator_persona": "",
                "topic": "why VRAM matters", "target_seconds": 8.0, "writer_model": "old", "notes": []})
    return out


class TestReviseScreenplay:
    @pytest.mark.asyncio
    async def test_one_call_returns_the_whole_revised_screenplay(self, chat):
        revised = {**_outline(), "title": "Why VRAM Really Matters"}
        revised["scenes"][0]["beats"] = _scene()["beats"]
        chat.side_effect = [json.dumps(revised)]
        out = await sp.revise_screenplay(_written(), "punchier title", site_config=_sc())
        assert out["title"] == "Why VRAM Really Matters"
        assert out["cast"] == ["dex", "nova"] and out["format"] == "dialogue"
        assert out["revised_with"] == "punchier title" and out["writer_model"] == "ollama/test-writer"
        prompt = chat.await_args.args[0]
        assert "punchier title" in prompt and '"Why VRAM Matters"' in prompt
        assert chat.await_args.kwargs["phase"] == "story_revise_screenplay"

    @pytest.mark.asyncio
    async def test_a_stranger_speaking_gets_one_repair(self, chat):
        bad = _outline()
        bad["scenes"][0]["beats"] = _scene((("zed", "Who am I?"),))["beats"]
        good = _outline()
        good["scenes"][0]["beats"] = _scene()["beats"]
        chat.side_effect = [json.dumps(bad), json.dumps(good)]
        out = await sp.revise_screenplay(_written(), "add a line", site_config=_sc())
        assert chat.await_count == 2 and "'zed' cannot speak" in chat.await_args_list[1].args[0]
        assert out["scenes"][0]["beats"][0]["speaker"] == "dex"

    @pytest.mark.asyncio
    async def test_a_note_is_required(self, chat):
        with pytest.raises(sp.ScreenplayError):
            await sp.revise_screenplay(_written(), "  ", site_config=_sc())
        assert chat.await_count == 0
