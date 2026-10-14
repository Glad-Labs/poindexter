"""A finished cartoon's YouTube package (services/video_package.py, chat drafting phase 7).

Pins what the chat card promises: the writer's title, description, tags and
thumbnail words are checked in code before anyone sees them; a revision
starts from the version it revises; nothing touches the video until a
version is kept, and keeping stores it exactly where the uploader reads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import creative_drafts as cd
from poindexter.services import video_package as vp
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config=extra)


SHOTS = {"shots": [
    {"idx": 0, "source": "generative", "characters": ["cinder", "bolt"], "intent": "Wide shot of a neon alley",
     "duration_s": 4.0, "narration_offset_s": 0.0},
    {"idx": 1, "source": "title_card", "duration_s": 2.0, "narration_offset_s": 4.0},
    {"idx": 2, "source": "presenter", "speaker": "cinder", "frame_prompt": "Cinder smiles at the clock",
     "duration_s": 3.0, "narration_offset_s": 6.0},
]}
SCRIPT = {"title": "Cinder at Midnight", "logline": "A street mechanic gets one night at the palace.",
          "cast": ["cinder"], "scenes": [{"summary": "Cinder fixes a drone", "turn": "the invite arrives",
                                          "beats": [{"kind": "line", "speaker": "cinder",
                                                     "text": "I have 12 minutes to get there."}]}]}
LIMITS = {"title_max": 70, "description_max": 700, "max_tags": 3, "hook_max": 32}


def _good(**over):
    reply = {"title": "Cinderella in Neon City: Cinder at Midnight",
             "description": "A street mechanic has 12 minutes to reach the palace.",
             "tags": ["cinderella", "cyberpunk fairytale", "animated short"],
             "thumbnail_text": "One night only", "thumbnail_shot": 2}
    reply.update(over)
    return reply


def _check(reply):
    return vp.check_package(reply, shot_ids={0, 2}, source_text=vp.script_text(SCRIPT), **LIMITS)


class TestRows:
    def test_only_drawn_shots_with_their_moment(self):
        rows = vp.shot_rows(SHOTS)
        assert [r["idx"] for r in rows] == [0, 2]
        assert rows[0]["who"] == "cinder, bolt" and rows[1]["who"] == "cinder"
        assert rows[1]["picture"] == "Cinder smiles at the clock" and rows[1]["start_s"] == 6.0


class TestCheck:
    def test_a_good_package_passes(self):
        assert _check(_good()) == ([], [])

    def test_each_problem_is_named(self):
        hard, _ = _check(_good(
            title="x" * 80, description="Watch at https://example.com, 99 seconds of fun",
            tags=["one, two"], thumbnail_shot=1,
        ))
        text = " ".join(hard)
        assert "80 characters" in text and "never cut" in text
        assert "links" in text and "'99'" in text
        assert "no commas" in text and "thumbnail_shot" in text

    def test_thumbnail_words_get_the_long_thumbnails_checks(self):
        hard, _ = _check(_good(thumbnail_text="Cinder at Midnight"))
        assert any("only repeats words from the title" in h for h in hard)

    def test_extra_tags_are_trimmed_softly(self):
        reply = _good(tags=["a", "b", "c", "d", "#b"])
        hard, soft = _check(reply)
        assert hard == [] and reply["tags"] == ["a", "b", "c"] and "kept the first 3" in soft[0]

    def test_an_unsearchable_title_is_soft(self):
        hard, soft = _check(_good(title="the night it all changed"))
        assert hard == [] and any("search" in s for s in soft)


class _Pool:
    def __init__(self, video):
        self.video = video
        self.executed: list = []

    async def fetchrow(self, sql, *args):
        return self.video

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


VIDEO = {"id": "aaaaaaaa-0000-0000-0000-000000000000", "title": "Cinder at Midnight",
         "description": "A street mechanic gets one night at the palace.",
         "storage_path": "/v/t.mp4", "metadata": json.dumps({"standalone": "true", "video_shot_list": SHOTS}),
         "approval": "pending"}


@pytest.fixture
def writer(monkeypatch, tmp_path):
    import poindexter.services.llm_text as llm_text
    import poindexter.services.screenplay as sp
    import poindexter.services.storyboard as sb

    chat = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", chat)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    monkeypatch.setattr(vp, "_kept_render", lambda task_id, sc: {"screenplay": SCRIPT, "shot_list": SHOTS})
    monkeypatch.setattr(vp, "_package_dir", lambda sc: str(tmp_path))
    drawn: list = []

    async def fake_compose(*, video_path, shot, planned_s, hook, site_config, out_path):
        drawn.append((shot["idx"], hook, planned_s))
        Path(out_path).write_bytes(b"jpg")
        return out_path

    monkeypatch.setattr(vp, "compose_thumbnail", fake_compose)
    monkeypatch.setattr(sb, "_upload", AsyncMock(return_value="https://cdn/drafts/d1/thumbnail.jpg"))
    chat.drawn = drawn
    return chat


class TestDraft:
    @pytest.mark.asyncio
    async def test_the_writer_sees_the_story_and_the_shots(self, writer):
        writer.side_effect = [json.dumps(_good())]
        out = await vp.draft_package(_Pool(dict(VIDEO)), task_id="t1", site_config=_sc(), upload_prefix="drafts/d1")
        prompt = writer.await_args.args[0]
        assert "2 | cinder | Cinder smiles at the clock" in prompt and "cinder: I have 12 minutes" in prompt
        assert "1 | " not in prompt.split("THE DRAWN SHOTS")[1]
        assert writer.await_args.kwargs["phase"] == "story_youtube_package"
        assert out["thumbnail_url"].endswith("thumbnail.jpg") and out["thumbnail_shot"] == 2
        assert writer.drawn == [(2, "One night only", 9.0)]

    @pytest.mark.asyncio
    async def test_a_revision_carries_the_previous_version_and_the_note(self, writer):
        writer.side_effect = [json.dumps(_good(title="Cinderella Rewired: Cinder at Midnight"))]
        out = await vp.draft_package(
            _Pool(dict(VIDEO)), task_id="t1", site_config=_sc(), upload_prefix="drafts/d2",
            previous=_good(), instruction="punchier title",
        )
        prompt = writer.await_args.args[0]
        assert "punchier title" in prompt and '"thumbnail_shot": 2' in prompt
        assert out["title"].startswith("Cinderella Rewired")

    @pytest.mark.asyncio
    async def test_a_bad_reply_gets_one_repair(self, writer):
        writer.side_effect = [json.dumps(_good(thumbnail_shot=9)), json.dumps(_good())]
        out = await vp.draft_package(_Pool(dict(VIDEO)), task_id="t1", site_config=_sc(), upload_prefix="p")
        assert out["thumbnail_shot"] == 2 and "thumbnail_shot" in writer.await_args.args[0]

    @pytest.mark.asyncio
    async def test_an_approved_video_cannot_be_repackaged(self, writer):
        with pytest.raises(vp.PackageError, match="already approved"):
            await vp.draft_package(_Pool({**VIDEO, "approval": "approved"}), task_id="t1",
                                   site_config=_sc(), upload_prefix="p")
        assert writer.await_count == 0

    @pytest.mark.asyncio
    async def test_no_video_says_so(self, writer):
        with pytest.raises(vp.PackageError, match="no finished cartoon"):
            await vp.draft_package(_Pool(None), task_id="t1", site_config=_sc(), upload_prefix="p")


class TestApply:
    @pytest.mark.asyncio
    async def test_keeping_stores_it_where_the_uploader_reads_it(self, monkeypatch, tmp_path):
        import poindexter.services.video_thumbnail as vt

        thumb = tmp_path / "d1.jpg"
        thumb.write_bytes(b"jpg")
        store = AsyncMock(return_value="asset-1")
        monkeypatch.setattr(vt, "store_thumbnail_asset", store)
        pool = _Pool(dict(VIDEO))
        out = await vp.apply_package(pool, {**_good(), "task_id": "t1", "thumbnail_path": str(thumb)})
        sql, args = pool.executed[0]
        assert "UPDATE media_assets SET title" in sql
        assert args[1] == _good()["title"] and args[2] == _good()["description"]
        meta = json.loads(args[3])
        assert meta[vp.PACKAGE_KEY]["tags"] == _good()["tags"]
        assert meta[vp.SOURCE_KEY] == {"title": VIDEO["title"], "logline": VIDEO["description"]}
        assert store.await_args.kwargs["video_recorded_now"] is True
        assert store.await_args.kwargs["meta"]["hook"] == "One night only"
        assert out["thumbnail_asset_id"] == "asset-1"

    @pytest.mark.asyncio
    async def test_a_missing_thumbnail_file_is_refused(self):
        with pytest.raises(vp.PackageError, match="thumbnail file is gone"):
            await vp.apply_package(_Pool(dict(VIDEO)), {**_good(), "task_id": "t1", "thumbnail_path": "/nope.jpg"})


class TestDrafts:
    @pytest.mark.asyncio
    async def test_a_package_draft_checks_the_video_in_the_chat_turn(self, monkeypatch):
        import poindexter.utils.uuid_prefix as up

        monkeypatch.setattr(up, "resolve_task_id_prefix", AsyncMock(return_value="t1-full"))
        monkeypatch.setattr(vp, "check_packageable", AsyncMock(side_effect=vp.PackageError("still rendering")))
        with pytest.raises(cd.DraftError, match="still rendering"):
            await cd.start_draft(None, kind="package", request={"task_id": "t1"}, site_config=_sc())
        with pytest.raises(cd.DraftError, match="task id"):
            await cd.start_draft(None, kind="package", request={}, site_config=_sc())

    @pytest.mark.asyncio
    async def test_keeping_a_package_applies_it(self, monkeypatch):
        apply = AsyncMock(return_value={"title": "T"})
        monkeypatch.setattr(vp, "apply_package", apply)
        draft = {"id": "0123abcd-0000", "kind": "package", "version": 2, "status": "ready",
                 "payload": {"task_id": "t1", "title": "T"}}
        monkeypatch.setattr(cd, "get_draft", AsyncMock(return_value=draft))
        monkeypatch.setattr(cd, "_finish", AsyncMock())
        out = await cd.keep_draft(None, "0123abcd", site_config=_sc())
        assert out["status"] == "kept" and out["payload"]["applied"] == {"title": "T"}

    def test_the_chat_reads_a_package_draft(self):
        text = cd.describe({"id": "0123abcd-0000", "kind": "package", "version": 1, "status": "ready",
                            "payload": {**_good(), "task_id": "t1-full", "thumbnail_picture": "Cinder smiles"}})
        assert "title: Cinderella in Neon City" in text and "thumbnail: shot 2 (Cinder smiles)" in text


def test_standalone_uploads_read_the_package_tags():
    from poindexter.services.jobs import media_distribute as md

    assert "'youtube_package'->'tags'" in md._APPROVED_UNDISPATCHED_SQL


class TestOlderRenders:
    @pytest.mark.asyncio
    async def test_a_render_without_a_kept_script_is_told_by_its_shots(self, writer, monkeypatch):
        monkeypatch.setattr(vp, "_kept_render", lambda task_id, sc: None)
        writer.side_effect = [json.dumps(_good(description="A street mechanic gets one night."))]
        await vp.draft_package(_Pool(dict(VIDEO)), task_id="t1", site_config=_sc(), upload_prefix="p")
        prompt = writer.await_args.args[0]
        story = prompt.split("THE STORY")[1].split("THE DRAWN SHOTS")[0]
        assert "- Wide shot of a neon alley" in story and "Cinder smiles at the clock" in story
        assert "cinder" in prompt.split("THE CAST")[1].split("THE STORY")[0]
