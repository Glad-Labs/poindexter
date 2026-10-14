"""Rendering a chat draft (creative_drafts phase 3).

A script draft renders through voicing and directing; a storyboard renders
exactly the shots it showed, with its own dialogue track. The plan is fixed
(no architect), validated like any plan, and the render card follows the
task to an in-chat Approve / Reject.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import creative_drafts as cd
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config={"persona.kai.voice_exaggeration": "0.75", **extra})


SCRIPT = {"title": "The Fox and the Crow", "cast": ["fox"], "scenes": []}
BOARD = {
    "title": "The Fox and the Crow", "style": "ink wash", "screenplay": SCRIPT,
    "shot_list": {"timing": "exact", "shots": [], "cast_portraits": {"fox": "u"}},
    "timeline": [{"kind": "line", "text": "Hello."}, {"kind": "action", "text": "bows"}],
    "audio_master_url": "https://cdn.example/drafts/b/dialogue.wav",
}


def _drafts(monkeypatch, **drafts):
    monkeypatch.setattr(cd, "get_draft", AsyncMock(side_effect=lambda pool, i, **k: drafts.get(i)))


class TestSpec:
    def test_a_script_is_voiced_and_directed_a_storyboard_is_not(self):
        script = cd.render_spec("screenplay", "d1")
        board = cd.render_spec("storyboard", "d1")
        assert [n["atom"] for n in script["nodes"]] == [
            "story.load_draft", "media.render_dialogue", "media.direct_screenplay", "media.score_screenplay",
            "media.transcribe_narration", "media.render_long_video", "media.qa", "media.persist",
            "media.director_notes",
        ]
        assert [n["atom"] for n in board["nodes"]][:3] == [
            "story.load_draft", "media.score_screenplay", "media.transcribe_narration",
        ]
        assert board["edges"][-1] == {"from": "notes", "to": "END"}

    def test_both_plans_validate_against_the_real_atoms(self):
        from poindexter.services.atom_registry import discover
        from poindexter.services.pipeline_architect import _validate_spec

        discover()
        for kind in ("screenplay", "storyboard"):
            ok, errors = _validate_spec(cd.render_spec(kind, "d1", cast_draft="c1"))
            assert ok, (kind, errors)

    def test_overrides_ride_on_the_plan(self):
        spec = cd.render_spec("storyboard", "d1", setting_overrides={"persona.kai.voice_exaggeration": 0.5})
        assert spec["setting_overrides"] == {"persona.kai.voice_exaggeration": 0.5}
        assert spec["nodes"][0]["config"] == {"creative_draft": "d1"}


class TestLoad:
    @pytest.mark.asyncio
    async def test_a_script_hands_over_its_screenplay_and_the_cast_style(self, monkeypatch):
        _drafts(monkeypatch,
                s1={"id": "s1", "kind": "screenplay", "status": "kept", "payload": SCRIPT},
                c1={"id": "c1", "kind": "cast", "status": "kept",
                    "payload": {"style": "ink wash", "cast_portraits": {"fox": "u"}}})
        out = await cd.load_for_render(None, "s1", site_config=_sc(), cast_draft="c1")
        assert out == {"screenplay": SCRIPT, "screenplay_visual_style": "ink wash", "cast_portraits": {"fox": "u"}}

    @pytest.mark.asyncio
    async def test_a_storyboard_hands_over_everything_it_was_made_with(self, monkeypatch, tmp_path):
        _drafts(monkeypatch, b1={"id": "b1", "kind": "storyboard", "status": "ready", "payload": BOARD})

        async def fake_download(url, dest):
            Path(dest).write_bytes(b"wav")
            return dest

        monkeypatch.setattr(cd, "_download", fake_download)
        out = await cd.load_for_render(None, "b1", site_config=_sc())
        # A copy, prepared to render from the storyboard's own pictures (test_storyboard_frames).
        assert out["video_shot_list"] == BOARD["shot_list"] and out["dialogue_timeline"] == BOARD["timeline"]
        assert out["video_long_script"] == "Hello." and out["screenplay_visual_style"] == "ink wash"
        assert out["cast_portraits"] == {"fox": "u"} and out["long_narration_audio_path"].endswith("dialogue.wav")

    @pytest.mark.asyncio
    async def test_only_a_ready_script_or_storyboard_renders(self, monkeypatch):
        _drafts(monkeypatch,
                w={"id": "w", "kind": "screenplay", "status": "working", "payload": {}},
                c={"id": "c", "kind": "cast", "status": "ready", "payload": {}},
                old={"id": "old", "kind": "storyboard", "status": "ready", "payload": {**BOARD, "audio_master_url": None}})
        for draft_id, match in (("w", "working"), ("c", "not a script"), ("old", "storyboard the scenes again")):
            with pytest.raises(cd.DraftError, match=match):
                await cd.load_for_render(None, draft_id, site_config=_sc())


class TestStart:
    @pytest.mark.asyncio
    async def test_queues_the_plan_and_reports_the_task(self, monkeypatch):
        from poindexter.services import chat_plans

        _drafts(monkeypatch, b1={"id": "b1", "kind": "storyboard", "status": "ready", "payload": BOARD})
        create = AsyncMock(return_value={"plan_id": "p1", "overrides": ["every step: persona.kai.voice_exaggeration = 0.5"]})
        run = AsyncMock(return_value={"task_id": "t1"})
        monkeypatch.setattr(chat_plans, "create_plan", create)
        monkeypatch.setattr(chat_plans, "run_plan", run)
        out = await cd.start_render(
            None, None, draft_id="b1", site_config=_sc(), conversation_id="cv", message_id="m",
            setting_overrides={"persona.kai.voice_exaggeration": 0.5}, niche_slug="lab",
        )
        assert out["task_id"] == "t1" and out["title"] == "The Fox and the Crow"
        spec = create.await_args.kwargs["spec"]
        assert spec["setting_overrides"] == {"persona.kai.voice_exaggeration": 0.5}
        assert run.await_args.kwargs["params"] == {"niche_slug": "lab"}

    @pytest.mark.asyncio
    async def test_a_refused_override_stops_before_queueing(self, monkeypatch):
        from poindexter.services import chat_plans

        _drafts(monkeypatch, b1={"id": "b1", "kind": "storyboard", "status": "ready", "payload": BOARD})
        create = AsyncMock()
        monkeypatch.setattr(chat_plans, "create_plan", create)
        with pytest.raises(cd.DraftError, match="cannot be overridden"):
            await cd.start_render(None, None, draft_id="b1", site_config=_sc(), conversation_id="cv",
                                  message_id="m", setting_overrides={"story_writer_model": "x"})
        create.assert_not_awaited()


class TestChat:
    @pytest.mark.asyncio
    async def test_render_draft_links_the_task_and_emits_a_render_card(self, monkeypatch):
        from poindexter.services import chat_tools as ct

        start = AsyncMock(return_value={"task_id": "t1234567", "plan_id": "p", "title": "Fable", "overrides": []})
        monkeypatch.setattr(cd, "start_render", start)
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None, conversation_id="cv", message_id="m")
        text = await ct._render_draft(ctx, draft_id="b1", setting_overrides='{"persona.kai.voice_exaggeration": 0.5}')
        assert ctx.linked_task_ids == ["t1234567"] and ctx.emitted_cards[0]["kind"] == "render"
        assert start.await_args.kwargs["setting_overrides"] == {"persona.kai.voice_exaggeration": 0.5}
        assert "t1234567" in text

    @pytest.mark.asyncio
    async def test_bad_override_json_is_a_tool_error(self):
        from poindexter.services import chat_tools as ct

        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None, conversation_id="cv", message_id="m")
        with pytest.raises(ct.ChatToolError, match="JSON object"):
            await ct._render_draft(ctx, draft_id="b1", setting_overrides="calmer please")


class TestAtom:
    @pytest.mark.asyncio
    async def test_the_atom_delegates_to_the_loader(self, monkeypatch):
        from poindexter.modules.content.atoms import story_load_draft as atom

        load = AsyncMock(return_value={"screenplay": SCRIPT})
        monkeypatch.setattr(cd, "load_for_render", load)
        out = await atom.run({"site_config": _sc(), "pool": object(), "creative_draft": "s1", "cast_draft": "c1"})
        assert out == {"screenplay": SCRIPT}
        assert load.await_args.args[1] == "s1" and load.await_args.kwargs["cast_draft"] == "c1"

    def test_its_outputs_are_declared_channels(self):
        from poindexter.modules.content.atoms import story_load_draft as atom
        from poindexter.services.template_runner import PipelineState

        assert set(atom.ATOM_META.produces) <= set(PipelineState.__annotations__)
