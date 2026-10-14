"""Chat drafting (services/creative_drafts.py): draft, look, revise, keep.

Pins what the chat and the draft card rely on: a draft is built in the
background and lands as ready or failed (never silently stuck); a revision is
a new version of a ready draft; keep makes a cast real and nothing before
that does; a cast draft waits while a video renders; and a draft card stays
nameable to the chat model after the turn ends.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from poindexter.services import creative_drafts as cd
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config=extra)


def _draft(**over):
    base = {"id": "0123abcd-0000-0000-0000-000000000000", "kind": "cast", "version": 1,
            "status": "ready", "note": "", "error": "", "request": {"topic": "a fable"},
            "payload": {}, "conversation_id": None, "parent_id": None}
    base.update(over)
    return base


@pytest.fixture
def db(monkeypatch):
    """Record the service's writes instead of reaching Postgres."""
    seen = SimpleNamespace(inserted=[], finished=[], notes=[], drafts={})

    async def insert(pool, *, kind, request, conversation_id, parent):
        row = _draft(kind=kind, request=request, status="working",
                     version=(parent["version"] + 1) if parent else 1,
                     id=f"{len(seen.inserted):08d}-0000-0000-0000-000000000000")
        seen.inserted.append({"row": row, "parent": parent})
        return row

    async def finish(pool, draft_id, *, status, payload=None, error=""):
        seen.finished.append((draft_id, status, payload, error))

    async def note(pool, draft_id, text):
        seen.notes.append(text)

    async def get(pool, draft_id, *, site_config=None):
        return seen.drafts.get(draft_id)

    monkeypatch.setattr(cd, "_insert", insert)
    monkeypatch.setattr(cd, "_finish", finish)
    monkeypatch.setattr(cd, "_note", note)
    monkeypatch.setattr(cd, "get_draft", get)
    return seen


async def _drain():
    while cd._JOBS:
        await asyncio.gather(*list(cd._JOBS))


class TestJobs:
    @pytest.mark.asyncio
    async def test_a_draft_starts_working_and_lands_ready(self, db, monkeypatch):
        monkeypatch.setattr(cd, "_build", AsyncMock(return_value={"style": "ink"}))
        row = await cd.start_draft(None, kind="cast", request={"topic": "a fable"}, site_config=_sc())
        assert row["status"] == "working"
        await _drain()
        assert db.finished == [(row["id"], "ready", {"style": "ink"}, "")]

    @pytest.mark.asyncio
    async def test_a_failure_lands_on_the_card_with_its_reason(self, db, monkeypatch):
        monkeypatch.setattr(cd, "_build", AsyncMock(side_effect=RuntimeError("no GPU")))
        row = await cd.start_draft(None, kind="cast", request={"topic": "t"}, site_config=_sc())
        await _drain()
        assert db.finished == [(row["id"], "failed", None, "RuntimeError: no GPU")]

    @pytest.mark.asyncio
    async def test_bad_requests_fail_in_the_chat_turn(self, db):
        with pytest.raises(cd.DraftError, match="topic"):
            await cd.start_draft(None, kind="cast", request={}, site_config=_sc())
        with pytest.raises(cd.DraftError, match="kind"):
            await cd.start_draft(None, kind="poem", request={"topic": "t"}, site_config=_sc())
        with pytest.raises(cd.DraftError, match="ghost"):
            await cd.start_draft(None, kind="screenplay", site_config=_sc(),
                                 request={"topic": "t", "screenplay_cast": "ghost"})
        assert db.inserted == []


class TestRevise:
    @pytest.mark.asyncio
    async def test_a_revision_is_the_next_version_of_a_ready_draft(self, db, monkeypatch):
        build = AsyncMock(return_value={"style": "ink"})
        monkeypatch.setattr(cd, "_build", build)
        parent = _draft()
        db.drafts["0123abcd"] = parent
        row = await cd.revise_draft(None, "0123abcd", "scarier wolf", site_config=_sc())
        await _drain()
        assert row["version"] == 2 and row["request"]["instruction"] == "scarier wolf"
        assert db.inserted[0]["parent"] is parent
        assert build.await_args.args[2] is parent

    @pytest.mark.asyncio
    async def test_only_a_ready_draft_can_be_revised(self, db):
        db.drafts["0123abcd"] = _draft(status="working")
        with pytest.raises(cd.DraftError, match="working"):
            await cd.revise_draft(None, "0123abcd", "x", site_config=_sc())


class TestKeep:
    @pytest.mark.asyncio
    async def test_keeping_a_cast_saves_its_personas(self, db, monkeypatch):
        import poindexter.services.casting as casting

        keep = AsyncMock(return_value={"screenplay_cast": ["wren"]})
        monkeypatch.setattr(casting, "keep_cast", keep)
        db.drafts["0123abcd"] = _draft(payload={"characters": []})
        out = await cd.keep_draft(None, "0123abcd", site_config=_sc())
        assert out["status"] == "kept" and out["payload"]["kept"] == {"screenplay_cast": ["wren"]}
        assert db.finished[-1][1] == "kept"

    @pytest.mark.asyncio
    async def test_a_working_draft_cannot_be_kept(self, db):
        db.drafts["0123abcd"] = _draft(status="working")
        with pytest.raises(cd.DraftError):
            await cd.keep_draft(None, "0123abcd", site_config=_sc())

    @pytest.mark.asyncio
    async def test_a_kept_draft_cannot_be_discarded(self, db):
        db.drafts["0123abcd"] = _draft(status="kept")
        with pytest.raises(cd.DraftError, match="kept"):
            await cd.discard_draft(None, "0123abcd")


class TestGpuWait:
    @pytest.mark.asyncio
    async def test_a_cast_draft_waits_while_a_video_renders(self, db, monkeypatch):
        busy = iter([True, True, False])
        monkeypatch.setattr(cd, "_gpu_busy", AsyncMock(side_effect=lambda *a: next(busy)))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        await cd._wait_for_gpu(None, "d1", _sc())
        assert db.notes[-1] == "" and db.notes.count("waiting for a video render to finish before drawing") == 2

    @pytest.mark.asyncio
    async def test_a_render_that_never_ends_fails_the_draft(self, db, monkeypatch):
        monkeypatch.setattr(cd, "_gpu_busy", AsyncMock(return_value=True))
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())
        with pytest.raises(cd.DraftError, match="held the GPU"):
            await cd._wait_for_gpu(None, "d1", _sc(creative_draft_gpu_wait_minutes="1"))


class TestText:
    def test_a_cast_reads_as_text(self):
        text = cd.describe(_draft(payload={"style": "ink", "characters": [
            {"slug": "wren", "name": "Wren", "role": "hero", "appearance": "red coat",
             "voice": "bf_emma", "energy": "high", "sample_line": "Hi", "reused": False}],
            "narrator": {"slug": "none"}}))
        assert "cast draft 0123abcd v1 (ready)" in text and "wren (new): Wren" in text

    def test_a_script_reads_as_text(self):
        text = cd.describe(_draft(kind="screenplay", payload={"title": "T", "logline": "L", "scenes": [
            {"scene_id": "s1", "setting": "a wood", "beats": [
                {"kind": "line", "speaker": "wren", "text": "Hello"}, {"kind": "action", "text": "runs"}]}]}))
        assert "wren: Hello" in text and "(runs)" in text

    def test_working_and_failed_say_so(self):
        assert "waiting for a video" in cd.describe(_draft(status="working", note="waiting for a video render"))
        assert cd.describe(_draft(status="failed", error="boom")).endswith("boom")

    def test_the_card_carries_only_the_id(self):
        assert cd.card(_draft()) == {"kind": "draft", "draft_id": _draft()["id"], "draft_kind": "cast",
                                     "version": 1, "status": "ready"}


class TestChat:
    @pytest.mark.asyncio
    async def test_draft_cast_emits_a_card_and_returns_at_once(self, monkeypatch):
        from poindexter.services import chat_tools as ct

        start = AsyncMock(return_value=_draft(status="working"))
        monkeypatch.setattr(cd, "start_draft", start)
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None, conversation_id="")
        text = await ct._draft_cast(ctx, topic="Red Riding Hood", style="ink", characters="dex")
        assert "0123abcd" in text and ctx.emitted_cards[0]["kind"] == "draft"
        sent = start.await_args.kwargs["request"]
        assert sent == {"topic": "Red Riding Hood", "screenplay_visual_style": "ink",
                        "screenplay_format": "story", "screenplay_cast": "dex"}

    @pytest.mark.asyncio
    async def test_a_draft_error_is_a_tool_error(self, monkeypatch):
        from poindexter.services import chat_tools as ct

        monkeypatch.setattr(cd, "start_draft", AsyncMock(side_effect=cd.DraftError("needs a topic")))
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None)
        with pytest.raises(ct.ChatToolError, match="needs a topic"):
            await ct._draft_screenplay(ctx, topic="", cast="dex")

    def test_the_tools_are_registered(self):
        from poindexter.services import chat_tools as ct

        assert {"draft_cast", "draft_screenplay", "revise_draft", "show_draft"} <= {t.name for t in ct.CHAT_TOOLS}

    def test_a_draft_card_stays_nameable_after_the_turn(self):
        from poindexter.services.chat_agent import _content_of

        text = _content_of({"parts": [
            {"type": "markdown", "text": "On its way."},
            {"type": "card", "card": cd.card(_draft())},
        ]})
        assert "[cast draft 0123abcd v1]" in text
