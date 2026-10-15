"""Storyboards (services/storyboard.py, chat drafting phase 2).

A storyboard voices the chosen scenes, plans the shots with the real director
and draws each one with the renderer's own functions, so the preview is the
picture the video starts from. A note redraws only the shots it changes, and
the shot list rides in the payload for the render (phase 3).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import creative_drafts as cd
from poindexter.services import storyboard as sb
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config={"site_name": "Glad Labs", **extra})


SCRIPT = {
    "title": "The Fox and the Crow",
    "cast": ["fox", "crow"],
    "scenes": [
        {"scene_id": "s1", "setting": "a wood", "beats": [{"kind": "line", "speaker": "fox", "text": "Hello."}]},
        {"scene_id": "s2", "setting": "a branch", "beats": [{"kind": "line", "speaker": "crow", "text": "Caw."}]},
    ],
}

TIMELINE = [
    {"kind": "line", "speaker": "fox", "text": "What a voice you must have.", "start_s": 0.0, "end_s": 2.5},
    {"kind": "action", "speaker": None, "text": "The crow preens.", "start_s": 2.5, "end_s": 4.0},
    {"kind": "line", "speaker": "crow", "text": "Caw!", "start_s": 4.0, "end_s": 5.0},
]


def _listing():
    return {
        "aspect": "16:9", "timing": "exact", "total_duration_s": 5.0,
        "director_model": "m", "director_prompt_version": "v",
        "director_decided_at": datetime.now(timezone.utc).isoformat(),
        "cast_portraits": {},
        "shots": [
            {"idx": 0, "duration_s": 2.5, "intent": "fox flatters", "source": "presenter",
             "narration_offset_s": 0.0, "speaker": "fox", "characters": ["fox"],
             "frame_prompt": "ink, fox alone below the tree", "prompt": "delivery: sly"},
            {"idx": 1, "duration_s": 1.5, "intent": "crow preens", "source": "image_gen",
             "narration_offset_s": 2.5, "characters": ["crow"], "prompt": "ink, the crow preening on a branch"},
            {"idx": 2, "duration_s": 1.0, "intent": "city b-roll", "source": "pexels",
             "narration_offset_s": 4.0, "query": "forest"},
        ],
    }


class TestSelectScenes:
    def test_by_id_number_or_all(self):
        assert [s["scene_id"] for s in sb.select_scenes(SCRIPT, ["s2"])["scenes"]] == ["s2"]
        assert [s["scene_id"] for s in sb.select_scenes(SCRIPT, ["1"])["scenes"]] == ["s1"]
        assert len(sb.select_scenes(SCRIPT, [])["scenes"]) == 2

    def test_an_unknown_scene_names_the_real_ones(self):
        with pytest.raises(sb.StoryboardError, match=r"\['s1', 's2'\]"):
            sb.select_scenes(SCRIPT, ["s9"])


class TestRows:
    def test_each_shot_carries_its_words_and_kind(self):
        rows = sb._rows(_listing(), TIMELINE, {0: "u0", 1: "u1"})
        assert [r["kind"] for r in rows] == ["talking", "still", "footage"]
        assert rows[0]["words"] == "fox: What a voice you must have."
        assert rows[1]["words"] == ""  # the action, no line
        assert rows[2]["words"] == "crow: Caw!"
        assert rows[0]["picture"] == "ink, fox alone below the tree"  # a talking shot's frame
        assert rows[2]["image_url"] is None


@pytest.fixture
def stage(monkeypatch):
    """Stub voicing, directing, drawing and storage; record what was drawn."""
    import poindexter.services.casting as casting
    import poindexter.services.dialogue_audio as da
    import poindexter.services.screenplay_director as sd
    from poindexter.services.gpu_scheduler import gpu

    drawn: list[int] = []
    seen: dict = {}

    async def fake_voice(screenplay, *, site_config, output_path):
        seen["voiced"] = [s["scene_id"] for s in screenplay["scenes"]]
        Path(output_path).write_bytes(b"wav")
        return {"audio_path": output_path, "timeline": TIMELINE, "duration_s": 5.0, "spoken_script": ""}

    async def fake_direct(screenplay, timeline, seconds, *, site_config, style, pool, cast_portraits=None, props=None):
        seen["style"] = style
        seen["cast_portraits"] = cast_portraits
        return _listing()

    async def fake_draw(shot, *, work, ctx, site_config, portraits):
        drawn.append(shot.idx)
        if sb._kind(shot) == sb.FOOTAGE:
            return None
        path = work / f"s{shot.idx}.png"
        path.write_bytes(b"png")
        return str(path)

    async def fake_upload(local, key, mime, site_config):
        return f"https://cdn.example/{key}"

    async def fake_mp3(src, dst):
        Path(dst).write_bytes(b"mp3")
        return True

    monkeypatch.setattr(da, "render_dialogue", fake_voice)
    monkeypatch.setattr(sd, "direct_screenplay", fake_direct)
    monkeypatch.setattr(sb, "_draw", fake_draw)
    monkeypatch.setattr(sb, "_upload", fake_upload)
    monkeypatch.setattr(sb, "_to_mp3", fake_mp3)
    monkeypatch.setattr(casting, "_ensure_voice_headroom", AsyncMock())
    monkeypatch.setattr(gpu, "_unload_comfyui", AsyncMock())
    seen["drawn"] = drawn
    return seen


class TestBuild:
    @pytest.mark.asyncio
    async def test_voices_directs_and_draws_the_chosen_scene(self, stage):
        out = await sb.build_storyboard(
            SCRIPT, scenes=["2"], site_config=_sc(), pool=None, upload_prefix="drafts/b1",
            style="ink wash", cast_portraits={"fox": "u"},
        )
        assert stage["voiced"] == ["s2"] and stage["style"] == "ink wash"
        assert stage["cast_portraits"] == {"fox": "u"}
        assert stage["drawn"] == [0, 1, 2]
        assert out["audio_url"] == "https://cdn.example/drafts/b1/dialogue.mp3"
        assert out["audio_master_url"] == "https://cdn.example/drafts/b1/dialogue.wav"
        assert [r["image_url"] for r in out["shots"]] == [
            "https://cdn.example/drafts/b1/shot_00.png", "https://cdn.example/drafts/b1/shot_01.png", None,
        ]
        assert out["shot_list"]["timing"] == "exact" and out["scenes"] == ["s2"]


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text
    import poindexter.services.screenplay as sp

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


class TestMissingPictures:
    """The Circuit and the Song (2026-10-15): the image server restarted mid-pass
    and the storyboard reported ready with 42 of 45 pictures missing."""

    def test_too_many_empty_pictures_fail_the_pass(self):
        from poindexter.schemas.video_shot_list import VideoShotList

        shot_list = VideoShotList.model_validate(_listing())
        sb._require_pictures(shot_list, {0: "u0", 1: "u1", 2: None}, _sc())  # footage needs no picture
        with pytest.raises(sb.StoryboardError, match=r"1 of 2 pictures could not be drawn \(shots \[1\]"):
            sb._require_pictures(shot_list, {0: "u0", 1: None}, _sc())
        sb._require_pictures(shot_list, {0: "u0", 1: None}, _sc(story_storyboard_max_missing="0.5"))


class TestRevise:
    @pytest.mark.asyncio
    async def test_a_note_redraws_only_the_shots_it_changes(self, stage, chat):
        board = await sb.build_storyboard(SCRIPT, scenes=[], site_config=_sc(), pool=None, upload_prefix="drafts/b1")
        stage["drawn"].clear()
        chat.side_effect = [json.dumps({"shots": [{"id": 0, "prompt": "ink, fox alone, wider, dusk"}]})]
        out = await sb.revise_storyboard(board, "shot 0 wider, at dusk", site_config=_sc(), pool=None,
                                         upload_prefix="drafts/b2")
        assert stage["drawn"] == [0]
        assert out["shot_list"]["shots"][0]["frame_prompt"] == "ink, fox alone, wider, dusk"
        assert out["shots"][0]["image_url"] == "https://cdn.example/drafts/b2/shot_00-v2.png"
        assert out["shots"][1]["image_url"] == board["shots"][1]["image_url"]  # kept
        assert out["revisions"] == [{"note": "shot 0 wider, at dusk", "shots": [0]}]
        assert "0 | talking | fox" in chat.await_args.args[0]
        assert "2 | footage" not in chat.await_args.args[0]  # nothing to draw there

    @pytest.mark.asyncio
    async def test_a_redrawn_picture_keeps_the_props_design(self, stage, chat):
        board = await sb.build_storyboard(SCRIPT, scenes=[], site_config=_sc(), pool=None, upload_prefix="drafts/b1")
        branch = {"name": "oak branch", "look": "a gnarled black oak branch"}
        board["shot_list"]["props"] = [branch]
        chat.side_effect = [json.dumps({"shots": [{"id": 1, "prompt": "ink, the crow on the branch, wings out"}]})]
        out = await sb.revise_storyboard(board, "shot 1 wings out", site_config=_sc(), pool=None,
                                         upload_prefix="drafts/b2")
        assert out["shot_list"]["shots"][1]["prompt"] == (
            "ink, the crow on the branch, wings out. The oak branch: a gnarled black oak branch"
        )

    @pytest.mark.asyncio
    async def test_a_redrawn_still_can_frame_its_characters_out(self, stage, chat):
        board = await sb.build_storyboard(SCRIPT, scenes=[], site_config=_sc(), pool=None, upload_prefix="drafts/b1")
        chat.side_effect = [json.dumps({"shots": [{"id": 1, "prompt": "ink, an empty branch", "visible": []}]})]
        out = await sb.revise_storyboard(board, "shot 1 the empty branch", site_config=_sc(), pool=None,
                                         upload_prefix="drafts/b2")
        assert out["shot_list"]["shots"][1]["characters"] == []

    def test_a_reply_may_change_only_the_motion(self):
        assert sb.check_revision({"shots": [{"id": 1, "motion": "a held camera"}]}, {0, 1}) == []
        assert any('"motion"' in e for e in sb.check_revision({"shots": [{"id": 1, "motion": 3}]}, {0, 1}))
        assert any('"prompt"' in e for e in sb.check_revision({"shots": [{"id": 1}]}, {0, 1}))
        listing = {"shots": [{"idx": 1, "source": "generative", "prompt": "p", "motion": "m", "characters": ["crow"]},
                             {"idx": 2, "source": "image_gen", "prompt": "q", "characters": []}]}
        redrawn, changed = sb.apply_revision(listing, {"shots": [{"id": 1, "motion": "held"}, {"id": 2, "motion": "x"}]})
        assert redrawn == set() and changed == {1, 2}
        assert listing["shots"][0]["motion"] == "held" and "motion" not in listing["shots"][1]  # a still has none

    def test_visible_names_only_the_cast(self):
        reply = {"shots": [{"id": 1, "prompt": "x", "visible": ["owl"]}]}
        assert any('"visible"' in e for e in sb.check_revision(reply, {0, 1}, {"fox", "crow"}))
        assert sb.check_revision({"shots": [{"id": 1, "prompt": "x", "visible": ["Crow"]}]}, {0, 1}, {"fox", "crow"}) == []

    def test_a_reply_must_name_real_drawable_shots(self):
        assert sb.check_revision({"shots": []}, {0, 1})
        assert any("no shot 7" in e for e in sb.check_revision({"shots": [{"id": 7, "prompt": "x"}]}, {0, 1}))
        assert any('"prompt"' in e for e in sb.check_revision({"shots": [{"id": 0, "prompt": " "}]}, {0, 1}))
        assert sb.check_revision({"shots": [{"id": "1", "prompt": "x"}]}, {0, 1}) == []


class TestAsADraft:
    @pytest.mark.asyncio
    async def test_a_storyboard_needs_a_ready_script(self, monkeypatch):
        drafts = {"aaaaaaaa": {"id": "aaaaaaaa-1", "kind": "screenplay", "status": "working", "payload": SCRIPT}}
        monkeypatch.setattr(cd, "get_draft", AsyncMock(side_effect=lambda pool, i, **k: drafts.get(i)))
        with pytest.raises(cd.DraftError, match="working"):
            await cd._check_storyboard_request(None, {"screenplay_draft": "aaaaaaaa"}, _sc())
        drafts["aaaaaaaa"]["status"] = "ready"
        with pytest.raises(cd.DraftError, match="no scene"):
            await cd._check_storyboard_request(None, {"screenplay_draft": "aaaaaaaa", "scenes": ["s9"]}, _sc())
        request = {"screenplay_draft": "aaaaaaaa", "scenes": ["2"]}
        await cd._check_storyboard_request(None, request, _sc())
        assert request["screenplay_draft"] == "aaaaaaaa-1"  # the full id

    @pytest.mark.asyncio
    async def test_the_cast_draft_carries_style_and_portraits(self, monkeypatch):
        drafts = {
            "script-1": {"id": "script-1", "kind": "screenplay", "status": "ready", "payload": SCRIPT},
            "cast-1": {"id": "cast-1", "kind": "cast", "status": "kept",
                       "payload": {"style": "ink wash", "cast_portraits": {"fox": "u"}}},
        }
        monkeypatch.setattr(cd, "get_draft", AsyncMock(side_effect=lambda pool, i, **k: drafts.get(i)))
        monkeypatch.setattr(cd, "_wait_for_gpu", AsyncMock())
        build = AsyncMock(return_value={"shots": []})
        monkeypatch.setattr(sb, "build_storyboard", build)
        monkeypatch.setattr(cd, "_note", AsyncMock())
        import poindexter.services.rough_cut as rc

        cut = AsyncMock(side_effect=lambda board, **kw: {**board, "rough_cut_url": "u"})
        monkeypatch.setattr(rc, "add_rough_cut", cut)
        draft = {"id": "b-1", "kind": "storyboard", "version": 1,
                 "request": {"screenplay_draft": "script-1", "cast_draft": "cast-1", "scenes": ["s1"]}}
        out = await cd._build(None, draft, None, _sc())
        assert out["rough_cut_url"] == "u" and cut.await_args.kwargs["upload_prefix"] == "drafts/b-1"
        kwargs = build.await_args.kwargs
        assert kwargs["style"] == "ink wash" and kwargs["cast_portraits"] == {"fox": "u"}
        assert kwargs["scenes"] == ["s1"] and kwargs["upload_prefix"] == "drafts/b-1"

    @pytest.mark.asyncio
    async def test_the_chat_tool_maps_its_arguments(self, monkeypatch):
        from poindexter.services import chat_tools as ct

        start = AsyncMock(return_value={"id": "bbbbbbbb-1", "kind": "storyboard", "version": 1, "status": "working"})
        monkeypatch.setattr(cd, "start_draft", start)
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None)
        text = await ct._storyboard_draft(ctx, screenplay_draft="aaaaaaaa", scenes="s1, 3", cast_draft="cccccccc")
        assert start.await_args.kwargs["kind"] == "storyboard"
        assert start.await_args.kwargs["request"] == {
            "screenplay_draft": "aaaaaaaa", "scenes": ["s1", "3"], "cast_draft": "cccccccc",
        }
        assert "few minutes" in text and ctx.emitted_cards[0]["draft_kind"] == "storyboard"
        await ct._storyboard_draft(ctx, screenplay_draft="aaaaaaaa", scenes="all")
        assert start.await_args.kwargs["request"]["scenes"] == []

    def test_a_storyboard_reads_as_text(self):
        text = cd.describe({"id": "bbbbbbbb-1", "kind": "storyboard", "version": 1, "status": "ready",
                            "payload": {"title": "T", "scenes": ["s1"], "style": "ink",
                                        "shots": sb._rows(_listing(), TIMELINE, {0: "u"})}})
        assert "shot 0 (talking, 2.5s, drawn): fox: What a voice" in text
        assert "shot 2 (footage, 1.0s, not drawn)" in text
