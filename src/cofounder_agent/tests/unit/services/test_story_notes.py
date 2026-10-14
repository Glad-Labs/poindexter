"""Story notes apply (chat drafting phase 10).

A story, pacing or sound note rewrites the script; the storyboard is then
made again, and only what the rewrite changed is redone. Pinned here: the
line cache keeps every unchanged line's take (and only those); an unchanged
scene's shots keep their pictures; a story revision versions the script
draft and points the storyboard at it; and the scope reaches the draft.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from poindexter.services import creative_drafts as cd
from poindexter.services import dialogue_audio as da
from poindexter.services import storyboard as sb
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config={
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true", "persona.dex.voice_id": "am_puck",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true", "persona.nova.voice_id": "af_nova",
        **extra,
    })


def _script(second_line: str) -> dict:
    return {"title": "t", "scenes": [
        {"scene_id": "s1", "setting": "a lab", "characters": ["dex"], "beats": [
            {"kind": "line", "speaker": "dex", "text": "hello there"}]},
        {"scene_id": "s2", "setting": "a roof", "characters": ["nova"], "beats": [
            {"kind": "line", "speaker": "nova", "text": second_line}]},
    ]}


@pytest.fixture
def ffmpeg(monkeypatch):
    """Files carry their own length: a clip's bytes are its seconds."""
    async def normalize(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes())

    async def silence(seconds, dst):
        Path(dst).write_text(str(seconds))

    async def concat(parts, dst, work):
        Path(dst).write_text(str(sum(float(Path(p).read_text()) for p in parts)))

    async def probe(path):
        return float(Path(path).read_text())

    monkeypatch.setattr(da, "_normalize", normalize)
    monkeypatch.setattr(da, "_silence", silence)
    monkeypatch.setattr(da, "_concat", concat)
    monkeypatch.setattr(da, "probe_seconds", probe)
    monkeypatch.setattr(da, "_reclaim_for_tts", AsyncMock())


class TestLineCache:
    @pytest.mark.asyncio
    async def test_only_changed_lines_are_voiced_again(self, ffmpeg, tmp_path):
        said: list[str] = []

        async def synthesize(text, persona, path, **kw):
            said.append(text)
            Path(path).write_text(str(len(text) / 10))
            return path

        cache = str(tmp_path / "lines")
        Path(cache).mkdir()
        first = await da.render_dialogue(_script("up here"), site_config=_sc(), output_path=str(tmp_path / "a.wav"),
                                         synthesize=synthesize, line_cache=cache)
        second = await da.render_dialogue(_script("look up"), site_config=_sc(), output_path=str(tmp_path / "b.wav"),
                                          synthesize=synthesize, line_cache=cache)
        assert said == ["hello there", "up here", "look up"]
        assert first["lines_reused"] == 0 and second["lines_reused"] == 1
        assert second["timeline"][0]["end_s"] == first["timeline"][0]["end_s"]

    def test_the_key_follows_the_voice_not_the_looks(self):
        from poindexter.services.persona_service import get_persona

        sc = _sc()
        dex = get_persona(sc, "dex")
        key = da.line_cache_key(dex, "hi", "medium", None, sc)
        assert key == da.line_cache_key(dex.__class__(**{**dex.__dict__, "appearance": "new coat"}), "hi", "medium", None, sc)
        assert key != da.line_cache_key(dex.__class__(**{**dex.__dict__, "voice_id": "bf_emma"}), "hi", "medium", None, sc)
        assert key != da.line_cache_key(dex, "hi", "high", None, sc)
        assert key != da.line_cache_key(dex, "hi", "medium", None, _sc(podcast_tts_voice="other"))

    def test_seams_do_not_touch_a_real_cache(self):
        assert da.line_cache_dir(_sc(story_line_cache_enabled="false")) is None


TIMELINE_OLD = [
    {"scene_id": "s1", "kind": "line", "start_s": 0.0, "end_s": 1.1},
    {"scene_id": "s2", "kind": "line", "start_s": 1.9, "end_s": 2.6},
]
TIMELINE_NEW = [
    {"scene_id": "s1", "kind": "line", "start_s": 0.0, "end_s": 1.1},
    {"scene_id": "s2", "kind": "line", "start_s": 1.9, "end_s": 3.5},
]


def _listing(*shots):
    return {"shots": [{"idx": i, **s} for i, s in enumerate(shots)]}


class TestCarryOver:
    def test_an_unchanged_scene_keeps_its_pictures(self):
        old = {
            "screenplay": _script("up here"), "timeline": TIMELINE_OLD,
            "shot_list": _listing(
                {"source": "presenter", "speaker": "dex", "characters": ["dex"], "narration_offset_s": 0.0,
                 "frame_prompt": "OLD dex frame"},
                {"source": "generative", "characters": ["nova"], "narration_offset_s": 1.9, "prompt": "OLD roof"},
            ),
            "shots": [{"idx": 0, "image_url": "u0"}, {"idx": 1, "image_url": "u1"}],
        }
        new_listing = _listing(
            {"source": "presenter", "speaker": "dex", "characters": ["dex"], "narration_offset_s": 0.0,
             "frame_prompt": "NEW dex frame"},
            {"source": "generative", "characters": ["nova"], "narration_offset_s": 1.9, "prompt": "NEW roof"},
        )
        urls, redraw, changed = sb.carry_over(old, new_listing, TIMELINE_NEW, _script("look up"))
        assert urls == {0: "u0"} and redraw == {1} and changed == ["s2"]
        assert new_listing["shots"][0]["frame_prompt"] == "OLD dex frame"
        assert new_listing["shots"][1]["prompt"] == "NEW roof"

    def test_a_scene_whose_shots_no_longer_line_up_is_redrawn(self):
        old = {"screenplay": _script("x"), "timeline": TIMELINE_OLD, "shots": [{"idx": 0, "image_url": "u0"}],
               "shot_list": _listing({"source": "presenter", "speaker": "dex", "characters": ["dex"],
                                      "narration_offset_s": 0.0})}
        new_listing = _listing({"source": "generative", "characters": ["dex"], "narration_offset_s": 0.0})
        urls, redraw, _ = sb.carry_over(old, new_listing, TIMELINE_NEW, _script("x"))
        assert urls == {} and redraw == {0}


@pytest.fixture
def db(monkeypatch):
    rows: dict = {}
    executed: list = []

    async def insert(pool, *, kind, request, conversation_id, parent):
        row = {"id": f"{kind}-{len(rows)}-0000", "kind": kind, "request": request, "status": "working",
               "version": (parent["version"] + 1) if parent else 1, "conversation_id": conversation_id}
        rows[row["id"]] = row
        return row

    async def finish(pool, draft_id, *, status, payload=None, error=""):
        rows.setdefault(draft_id, {}).update(status=status, payload=payload)

    class Pool:
        async def execute(self, sql, *args):
            executed.append((sql, args))

    monkeypatch.setattr(cd, "_insert", insert)
    monkeypatch.setattr(cd, "_finish", finish)
    monkeypatch.setattr(cd, "_note", AsyncMock())
    monkeypatch.setattr(cd, "_wait_for_gpu", AsyncMock())
    return rows, executed, Pool()


class TestStoryRevision:
    @pytest.mark.asyncio
    async def test_the_script_is_versioned_and_the_storyboard_follows_it(self, db, monkeypatch):
        import poindexter.services.rough_cut as rc
        import poindexter.services.screenplay as sp

        rows, executed, pool = db
        script = {"id": "script-1-0000", "kind": "screenplay", "status": "ready", "version": 1,
                  "request": {"topic": "t"}, "payload": _script("up here"), "conversation_id": "c1"}
        monkeypatch.setattr(cd, "get_draft", AsyncMock(return_value=script))
        monkeypatch.setattr(sp, "revise_screenplay", AsyncMock(return_value=_script("look up")))
        restory = AsyncMock(return_value={"shots": []})
        monkeypatch.setattr(sb, "restory_storyboard", restory)
        monkeypatch.setattr(rc, "add_rough_cut", AsyncMock(side_effect=lambda board, **kw: board))
        parent = {"payload": {"scenes": ["s1", "s2"]}}
        draft = {"id": "board-2", "kind": "storyboard", "conversation_id": "c1",
                 "request": {"screenplay_draft": "script-1", "instruction": "a sharper ending", "scope": "story"}}
        out = await cd._build(pool, draft, parent, _sc())
        new_id = out["script_draft"]["id"]
        assert rows[new_id]["status"] == "ready" and rows[new_id]["payload"] == _script("look up")
        assert out["script_draft"]["version"] == 2
        assert json.loads(executed[0][1][1]) == {"screenplay_draft": new_id}
        assert restory.await_args.args[1] == _script("look up")
        assert restory.await_args.kwargs["note"] == "a sharper ending"

    @pytest.mark.asyncio
    async def test_scope_story_reaches_only_a_storyboard(self, db, monkeypatch):
        rows, _, _ = db
        monkeypatch.setattr(cd, "_spawn", lambda coro: coro.close())
        board = {"id": "b-1-0000", "kind": "storyboard", "status": "ready", "version": 1,
                 "request": {"screenplay_draft": "s"}, "conversation_id": None}
        script = {**board, "id": "s-1-0000", "kind": "screenplay", "request": {"topic": "t"}}
        for parent, expect in ((board, "story"), (script, None)):
            monkeypatch.setattr(cd, "get_draft", AsyncMock(return_value=parent))
            row = await cd.revise_draft(None, parent["id"], "sharper", site_config=_sc(), scope="story")
            assert row["request"].get("scope") == expect
        with pytest.raises(cd.DraftError, match="scope"):
            await cd.revise_draft(None, "b-1", "x", site_config=_sc(), scope="vibes")

    @pytest.mark.asyncio
    async def test_the_chat_tool_passes_the_scope(self, monkeypatch):
        from poindexter.services import chat_tools as ct

        revise = AsyncMock(return_value={"id": "b-2-0000", "kind": "storyboard", "version": 2, "status": "working"})
        monkeypatch.setattr(cd, "revise_draft", revise)
        ctx = ct.ChatToolContext(db_service=None, site_config=_sc(), pool=None)
        await ct._revise_draft(ctx, draft_id="b-1", instruction="sharper", scope="story")
        assert revise.await_args.kwargs["scope"] == "story"
