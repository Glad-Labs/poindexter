"""Render revisions (chat drafting phase 4): redo only the shots a note is about.

A screenplay render keeps each shot's clip under a key over its render inputs;
a revision rewrites the shots a note names, keys only those differently, and
re-renders under the source's cache so every other clip is reused as final.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from poindexter.schemas.video_shot_list import VideoShotList
from poindexter.services import render_revision as rr
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import clip_cache


def _listing():
    return {
        "aspect": "16:9", "timing": "exact", "total_duration_s": 5.0,
        "director_model": "m", "director_prompt_version": "v",
        "director_decided_at": datetime.now(timezone.utc).isoformat(),
        "shots": [
            {"idx": 0, "duration_s": 2.5, "intent": "fox flatters", "source": "presenter",
             "narration_offset_s": 0.0, "speaker": "fox", "characters": ["fox"],
             "frame_prompt": "ink, fox alone below the tree", "prompt": "sly"},
            {"idx": 1, "duration_s": 2.5, "intent": "crow preens", "source": "image_gen",
             "narration_offset_s": 2.5, "characters": ["crow"], "prompt": "ink, the crow preening"},
        ],
    }


TIMELINE = [{"kind": "line", "speaker": "fox", "text": "What a voice.", "start_s": 0.0, "end_s": 2.5}]


def _shots():
    return VideoShotList.model_validate(_listing()).shots


class TestClipCache:
    def test_a_kept_clip_is_reused_until_its_inputs_change(self, tmp_path):
        shots = _shots()
        clip = tmp_path / "render" / "c0.mp4"
        clip.parent.mkdir()
        clip.write_bytes(b"mp4")
        cache = tmp_path / "cache"
        res = SimpleNamespace(success=True, clip_path=str(clip), duration_s=2.5)
        assert clip_cache.save(str(cache), [(shots[0], res, "primary")]) == 1
        work = tmp_path / "work"
        work.mkdir()
        assert set(clip_cache.reusable(str(cache), shots, work)) == {0}
        changed = shots[0].model_copy(update={"frame_prompt": "ink, fox alone, wider"})
        assert clip_cache.reusable(str(cache), [changed], work) == {}

    def test_fallbacks_are_not_kept(self, tmp_path):
        clip = tmp_path / "c.mp4"
        clip.write_bytes(b"x")
        res = SimpleNamespace(success=True, clip_path=str(clip), duration_s=1.0)
        saved = clip_cache.save(str(tmp_path / "cache"), [(_shots()[1], res, "card"), (_shots()[0], res, "substitute")])
        assert saved == 0

    def test_inputs_round_trip(self, tmp_path):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"wav")
        clip_cache.save_inputs(str(tmp_path / "c"), audio_path=str(audio), caption_path=None,
                               shot_list=_listing(), timeline=TIMELINE, screenplay={"title": "Fable"})
        kept = clip_cache.load_inputs(str(tmp_path / "c"))
        assert kept["shot_list"]["timing"] == "exact" and kept["timeline"] == TIMELINE
        assert kept["audio_path"].endswith("dialogue.wav") and kept["caption_path"] is None
        with pytest.raises(FileNotFoundError):
            clip_cache.load_inputs(str(tmp_path / "missing"))


class TestRenderPasses:
    @pytest.mark.asyncio
    async def test_a_kept_clip_is_not_rescored_and_finalizes_as_is(self):
        from poindexter.services.video_renderers import shot_list_renderer as slr

        st = slr._ShotState(
            shot=_shots()[1], is_reused=False, rung="kept",
            result=slr.ShotRenderResult(idx=1, source="image_gen", success=True, clip_path="/k.mp4", duration_s=2.5),
        )
        qa = slr._QAConfig(enabled=True, threshold=70.0, max_retries=1)
        await slr._score_pass([st], qa=qa, site_config=None, pool=None)  # would call the scorer if not skipped
        assert st.qa is None and not slr._needs_repair(st, qa=qa)
        out = await slr._finalize_pass([st], qa=qa, pool=None, post_id="t")
        assert out[0].clip_path == "/k.mp4"


def _sc(tmp_path, **extra):
    return SiteConfig(initial_config={"video_clip_cache_dir": str(tmp_path / "clips"), **extra})


class TestCacheDir:
    def test_only_a_screenplay_long_render_keeps_clips(self, tmp_path):
        from poindexter.modules.content.atoms._media_render import _clip_cache_dir

        exact = SimpleNamespace(timing="exact")
        assert _clip_cache_dir({"task_id": "t1"}, exact, "long_video_path", _sc(tmp_path)) == str(tmp_path / "clips" / "t1")
        assert _clip_cache_dir({"task_id": "t1"}, exact, "short_video_path", _sc(tmp_path)) is None
        assert _clip_cache_dir({"task_id": "t1"}, SimpleNamespace(timing="fit"), "long_video_path", _sc(tmp_path)) is None
        assert _clip_cache_dir({"task_id": "t1"}, exact, "long_video_path",
                               _sc(tmp_path, video_clip_cache_enabled="false")) is None
        # A revision renders under its source's key.
        assert _clip_cache_dir({"task_id": "t2", "video_clip_cache_key": "t1"}, exact, "long_video_path",
                               _sc(tmp_path)).endswith("/t1")


@pytest.fixture
def kept_render(tmp_path):
    root = tmp_path / "clips"
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")
    clip_cache.save_inputs(str(root / "t1"), audio_path=str(audio), caption_path=None,
                           shot_list=_listing(), timeline=TIMELINE, screenplay={"title": "Fable"})
    return root


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text
    import poindexter.services.screenplay as sp

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


class TestRevision:
    @pytest.mark.asyncio
    async def test_a_note_rewrites_only_its_shots(self, kept_render, chat, tmp_path):
        chat.side_effect = [json.dumps({"shots": [{"id": 0, "prompt": "ink, fox alone, hands out of frame"}]})]
        plan = await rr.plan_revision(None, task_id="t1", instruction="fox's hands lower", site_config=_sc(tmp_path))
        assert plan["key"] == "t1" and plan["changed"] == [0] and plan["title"] == "Fable"
        revised = json.loads((kept_render / "t1" / plan["shot_list_file"]).read_text())
        assert revised["shots"][0]["frame_prompt"] == "ink, fox alone, hands out of frame"
        assert revised["shots"][1]["prompt"] == "ink, the crow preening"  # untouched
        assert "0 | talking | fox | fox: What a voice." in chat.await_args.args[0]

    @pytest.mark.asyncio
    async def test_a_revision_of_a_revision_finds_the_source_cache(self, kept_render, tmp_path):
        (kept_render / "refs").mkdir()
        (kept_render / "refs" / "t2.txt").write_text("t1")
        assert rr.cache_key_for("t2", _sc(tmp_path)) == "t1"
        with pytest.raises(rr.RevisionError, match="kept no shot clips"):
            rr.cache_key_for("t9", _sc(tmp_path))

    def test_the_revision_state_reuses_the_track_and_names_the_cache(self, kept_render, tmp_path):
        (kept_render / "t1" / "revisions").mkdir()
        (kept_render / "t1" / "revisions" / "r.json").write_text(json.dumps(_listing()))
        out = rr.load_revision_state("t1", "revisions/r.json", site_config=_sc(tmp_path))
        assert out["video_clip_cache_key"] == "t1" and out["long_narration_audio_path"].endswith("dialogue.wav")
        assert out["screenplay"]["title"] == "Fable (revised)" and out["dialogue_timeline"] == TIMELINE
        with pytest.raises(rr.RevisionError):
            rr.load_revision_state("t1", "../../etc/passwd", site_config=_sc(tmp_path))

    def test_the_plan_validates_against_the_real_atoms(self):
        from poindexter.services.atom_registry import discover
        from poindexter.services.pipeline_architect import _validate_spec

        discover()
        spec = rr.revision_spec("t1", "revisions/r.json", setting_overrides={"persona.kai.voice_exaggeration": 0.5})
        ok, errors = _validate_spec(spec)
        assert ok, errors
        assert [n["atom"] for n in spec["nodes"]] == [
            "media.load_render_revision", "media.render_long_video", "media.qa", "media.persist",
            "media.director_notes",
        ]

    @pytest.mark.asyncio
    async def test_start_queues_and_records_the_source(self, kept_render, chat, monkeypatch, tmp_path):
        from poindexter.services import chat_plans

        chat.side_effect = [json.dumps({"shots": [{"id": 1, "prompt": "ink, the crow, closer"}]})]
        monkeypatch.setattr(chat_plans, "create_plan", AsyncMock(return_value={"plan_id": "p", "overrides": []}))
        monkeypatch.setattr(chat_plans, "run_plan", AsyncMock(return_value={"task_id": "t2"}))
        out = await rr.start_render_revision(
            None, None, task_id="t1", instruction="crow closer", site_config=_sc(tmp_path),
            conversation_id="cv", message_id="m",
        )
        assert out["task_id"] == "t2" and out["changed"] == [1]
        assert (kept_render / "refs" / "t2.txt").read_text() == "t1"

    @pytest.mark.asyncio
    async def test_a_refused_override_stops_before_the_writer(self, kept_render, chat, tmp_path):
        with pytest.raises(rr.RevisionError, match="cannot be overridden"):
            await rr.start_render_revision(
                None, None, task_id="t1", instruction="x", site_config=_sc(tmp_path),
                conversation_id="cv", message_id="m", setting_overrides={"story_writer_model": "x"},
            )
        assert chat.await_count == 0


class TestChat:
    @pytest.mark.asyncio
    async def test_revise_render_emits_a_render_card(self, monkeypatch, tmp_path):
        from poindexter.services import chat_tools as ct

        monkeypatch.setattr("poindexter.utils.uuid_prefix.resolve_task_id_prefix", AsyncMock(return_value="t1-full"))
        start = AsyncMock(return_value={"task_id": "t2abcdef", "changed": [0, 3], "title": "Fable (revised)", "overrides": []})
        monkeypatch.setattr(rr, "start_render_revision", start)
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(tmp_path), pool=None, conversation_id="cv", message_id="m")
        text = await ct._revise_render(ctx, task_id="t1", instruction="hands lower")
        assert "shot(s) 0, 3" in text and ctx.linked_task_ids == ["t2abcdef"]
        assert ctx.emitted_cards[0] == {"kind": "render", "task_id": "t2abcdef", "title": "Fable (revised)",
                                        "overrides": [], "revises": "t1-full"}
        assert start.await_args.kwargs["task_id"] == "t1-full"

    def test_the_atom_outputs_are_declared_channels(self):
        from poindexter.modules.content.atoms import media_load_render_revision as atom
        from poindexter.services.template_runner import PipelineState

        assert set(atom.ATOM_META.produces) | set(atom.ATOM_META.requires) <= set(PipelineState.__annotations__)
