"""Director's notes (services/director_notes.py, chat drafting phase 8).

Pins what the render card and the chat rely on: the judge's countable checks
become named flaws; a shot the judge could not read is marked unread, never
clean; the writer's notes name real shots and a redo names only drawn ones;
the review lands on the video row as ready or failed; and picked redo notes
become exactly the note a render revision takes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from poindexter.services import director_notes as dn
from poindexter.services.site_config import SiteConfig


def _sc(**extra):
    return SiteConfig(initial_config={"qa_vision_model": "ollama/judge", **extra})


SHOTS = {"shots": [
    {"idx": 0, "source": "generative", "characters": ["cinder"], "intent": "Cinder fixes a drone",
     "duration_s": 4.0, "narration_offset_s": 0.0},
    {"idx": 1, "source": "title_card", "duration_s": 2.0, "narration_offset_s": 4.0},
    {"idx": 2, "source": "presenter", "speaker": "cinder", "frame_prompt": "Cinder at the clock",
     "duration_s": 4.0, "narration_offset_s": 6.0},
]}
TIMELINE = [{"kind": "line", "speaker": "cinder", "text": "Twelve minutes.", "start_s": 6.5, "end_s": 8.0}]
SCRIPT = {"title": "Cinder at Midnight", "logline": "One night at the palace.", "cast": ["cinder"],
          "scenes": [{"summary": "Cinder races the clock", "turn": "she makes it",
                      "beats": [{"kind": "line", "speaker": "cinder", "text": "Twelve minutes."}]}]}


class TestLook:
    def test_failed_checks_become_named_flaws(self):
        look = dn.parse_shot_look(
            'ok {"hands_ok": false, "face_ok": true, "mouth_ok": true, "strangers": 2, '
            '"text_ok": false, "moves_ok": true, "matches": false, "note": "a melted hand"}'
        )
        assert look["read"] and look["note"] == "a melted hand"
        assert look["flaws"] == ["hands", "garbled writing", "does not show what it should", "2 stranger(s) in frame"]

    def test_an_unreadable_reply_is_unread_not_clean(self):
        assert dn.parse_shot_look("no json here")["read"] is False

    def test_lines_come_from_the_timeline_overlap(self):
        assert dn.shot_lines(SHOTS, TIMELINE) == {0: "", 1: "", 2: "cinder: Twelve minutes."}

    def test_no_timeline_means_unknown_lines_not_silence(self):
        assert set(dn.shot_lines(SHOTS, []).values()) == {dn.UNKNOWN_LINE}


class TestCheck:
    def _check(self, reply, max_notes=8):
        return dn.check_notes(reply, shot_ids={0, 1, 2}, drawn_ids={0, 2}, max_notes=max_notes)

    def test_good_notes_pass_and_are_numbered(self):
        reply = {"verdict": "It lands.", "strengths": ["the clock"], "notes": [
            {"kind": "redo", "shots": [2], "problem": "hand melts", "fix": "hands out of frame"},
            {"kind": "pacing", "shots": [1], "problem": "card lingers", "fix": "shorter"}]}
        assert self._check(reply) == ([], [])
        assert [n["n"] for n in reply["notes"]] == [1, 2]

    def test_each_problem_is_named(self):
        reply = {"verdict": "", "notes": [
            {"kind": "redo", "shots": [1], "problem": "x", "fix": "y"},
            {"kind": "vibes", "shots": [9], "problem": "", "fix": ""}]}
        text = " ".join(self._check(reply)[0])
        assert "verdict" in text and "not drawn pictures" in text and "kind" in text
        assert "[9]" in text and '"problem" and "fix"' in text

    def test_extra_notes_are_trimmed_softly(self):
        reply = {"verdict": "v", "notes": [{"kind": "sound", "shots": [], "problem": "p", "fix": "f"}] * 3}
        hard, soft = self._check(reply, max_notes=2)
        assert hard == [] and len(reply["notes"]) == 2 and "first 2" in soft[0]


class TestTable:
    def test_the_writer_sees_what_the_judge_saw(self):
        from poindexter.services.video_package import shot_rows

        table = dn.shot_table(
            shot_rows(SHOTS), SHOTS["shots"], {2: "cinder: Twelve minutes."},
            {0: {"read": True, "flaws": [], "note": ""},
             2: {"read": False, "flaws": [], "note": "vision call failed"}}, scale=1.5,
        )
        rows = table.splitlines()
        assert rows[0].endswith("| clean") and rows[0].startswith("0 | 0.0s | 6.0s | drawn")
        assert "not a drawn picture" in rows[1]
        assert "could not be read (vision call failed)" in rows[2] and "9.0s" in rows[2]


class TestReview:
    @pytest.mark.asyncio
    async def test_screens_each_drawn_shot_then_writes_notes(self, monkeypatch):
        import poindexter.services.llm_text as llm_text
        import poindexter.services.screenplay as sp
        from poindexter.services import video_package as vp
        from poindexter.services.video_renderers import shot_vision_qa as sq

        monkeypatch.setattr(vp, "load_video", AsyncMock(return_value={
            "title": "Cinder at Midnight", "description": "", "storage_path": "/v/t.mp4",
            "metadata": {"video_shot_list": SHOTS}}))
        monkeypatch.setattr(vp, "_kept_render", lambda t, sc: {"screenplay": SCRIPT, "shot_list": SHOTS,
                                                               "timeline": TIMELINE})
        monkeypatch.setattr(sq, "_probe_seconds", AsyncMock(return_value=20.0))
        sheets: list = []

        async def sheet(video, times, tag, *, crop_bottom=0.0):
            assert crop_bottom == 0.22  # the burned-in captions are cut off
            sheets.append((tag, [round(t, 2) for t in times]))
            return f"/tmp/{tag}.png"

        monkeypatch.setattr(sq, "sheet_at", sheet)
        monkeypatch.setattr(sq, "_remove_quietly", lambda p: None)
        judge = AsyncMock(side_effect=[
            ('{"hands_ok": true, "face_ok": true, "mouth_ok": true, "strangers": 0, "text_ok": true, '
             '"moves_ok": true, "matches": true, "note": "lively"}', ""),
            ('{"hands_ok": false, "face_ok": true, "mouth_ok": true, "strangers": 0, "text_ok": true, '
             '"moves_ok": true, "matches": true, "note": "melted hand"}', ""),
        ])
        monkeypatch.setattr(sq, "_vision_text", judge)
        chat = AsyncMock(return_value=json.dumps({"verdict": "Charming.", "strengths": ["pace"], "notes": [
            {"kind": "redo", "shots": [2], "problem": "the hand melts", "fix": "hands below frame"}]}))
        monkeypatch.setattr(llm_text, "ollama_chat_text", chat)
        monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")

        out = await dn.review_render(object(), task_id="t1", site_config=_sc(director_notes_frames_per_shot="2"))
        # 20 s rendered over 10 s planned: each window is doubled.
        assert sheets == [("director0", [2.0, 6.0]), ("director2", [14.0, 18.0])]
        assert "Heard over it: cinder: Twelve minutes." in judge.await_args_list[1].kwargs["prompt"]
        prompt = chat.await_args.args[0]
        assert "flaws: hands (melted hand)" in prompt and "20 seconds" in prompt
        assert out["notes"][0]["n"] == 1 and out["flawed_shots"] == {"2": ["hands"]}
        assert out["shots_reviewed"] == 2 and out["shots_unread"] == 0

    @pytest.mark.asyncio
    async def test_no_vision_model_is_refused(self, monkeypatch):
        with pytest.raises(dn.ReviewError, match="qa_vision_model"):
            await dn.look_at_shots(video_path="/v", rows=[{"idx": 0}], lines={}, planned_s=1.0,
                                   site_config=SiteConfig(initial_config={}), pool=None)


class _Pool:
    def __init__(self, stored=None):
        self.stored = stored
        self.writes: list = []

    async def execute(self, sql, task_id, value):
        self.writes.append(json.loads(value))

    async def fetchval(self, sql, task_id):
        return self.stored


class TestStore:
    @pytest.mark.asyncio
    async def test_a_review_lands_ready(self, monkeypatch):
        monkeypatch.setattr(dn, "review_render", AsyncMock(return_value={"notes": [], "flawed_shots": {}}))
        pool = _Pool()
        out = await dn.run_review(pool, task_id="t1", site_config=_sc())
        assert [w["status"] for w in pool.writes] == ["working", "ready"] and out["status"] == "ready"

    @pytest.mark.asyncio
    async def test_a_failure_lands_with_its_reason(self, monkeypatch):
        monkeypatch.setattr(dn, "review_render", AsyncMock(side_effect=dn.ReviewError("no shots")))
        pool = _Pool()
        out = await dn.run_review(pool, task_id="t1", site_config=_sc())
        assert out["status"] == "failed" and "no shots" in pool.writes[-1]["error"]

    @pytest.mark.asyncio
    async def test_a_stale_working_review_reads_as_failed(self):
        pool = _Pool(json.dumps({"status": "working", "started_at": "2026-01-01T00:00:00+00:00"}))
        review = await dn.get_review(pool, task_id="t1", site_config=_sc())
        assert review["status"] == "failed" and "interrupted" in review["error"]


REVIEW = {"status": "ready", "notes": [
    {"n": 1, "kind": "redo", "shots": [2, 3], "problem": "hand melts.", "fix": "hands below frame."},
    {"n": 2, "kind": "story", "shots": [], "problem": "flat ending.", "fix": "a callback."},
]}


class TestApply:
    def test_picked_redo_notes_become_the_revision_note(self):
        assert dn.revision_instruction(REVIEW, [1]) == "Shots 2, 3: hand melts. Fix: hands below frame."

    def test_story_notes_and_unknown_numbers_are_refused(self):
        with pytest.raises(dn.ReviewError, match="script"):
            dn.revision_instruction(REVIEW, [2])
        with pytest.raises(dn.ReviewError, match="no note"):
            dn.revision_instruction(REVIEW, [7])

    @pytest.mark.asyncio
    async def test_the_chat_tool_runs_a_revision(self, monkeypatch):
        import poindexter.utils.uuid_prefix as up
        from poindexter.services import chat_tools as ct

        monkeypatch.setattr(up, "resolve_task_id_prefix", AsyncMock(return_value="t1-full"))
        monkeypatch.setattr(dn, "get_review", AsyncMock(return_value=REVIEW))
        revise = AsyncMock(return_value="queued")
        monkeypatch.setattr(ct, "_revise_render", revise)
        from types import SimpleNamespace

        ctx = SimpleNamespace(pool=object(), site_config=_sc())
        assert await ct._apply_director_notes(ctx, task_id="t1", notes="all") == "queued"
        assert revise.await_args.args == (ctx,)
        assert revise.await_args.kwargs == {"task_id": "t1-full",
                                            "instruction": "Shots 2, 3: hand melts. Fix: hands below frame."}


class TestAtom:
    @pytest.mark.asyncio
    async def test_off_does_nothing(self, monkeypatch):
        from poindexter.modules.content.atoms import media_director_notes as atom

        run = AsyncMock()
        monkeypatch.setattr(dn, "run_review", run)
        out = await atom.run({"site_config": _sc(director_notes_enabled="false"), "task_id": "t1", "pool": object()})
        assert out == {"director_notes": {"status": "off"}} and run.await_count == 0

    @pytest.mark.asyncio
    async def test_a_render_ends_with_its_notes(self, monkeypatch):
        from poindexter.modules.content.atoms import media_director_notes as atom

        monkeypatch.setattr(dn, "run_review", AsyncMock(return_value={"status": "ready", "notes": [1, 2]}))
        out = await atom.run({"site_config": _sc(), "task_id": "t1", "pool": object()})
        assert out == {"director_notes": {"status": "ready", "notes": 2}}

    def test_outputs_are_declared_channels(self):
        from poindexter.modules.content.atoms import media_director_notes as atom
        from poindexter.services.template_runner import PipelineState

        assert set(atom.ATOM_META.produces) <= set(PipelineState.__annotations__)


class TestChains:
    def test_draft_renders_and_revisions_end_with_notes(self):
        from poindexter.services import creative_drafts as cd
        from poindexter.services import render_revision as rr

        for spec in (cd.render_spec("screenplay", "d1"), rr.revision_spec("k", "revisions/r.json")):
            assert spec["nodes"][-1]["atom"] == "media.director_notes"

    def test_the_model_can_name_a_render_after_the_turn(self):
        from poindexter.services.chat_agent import _content_of

        text = _content_of({"parts": [{"type": "card", "card": {"kind": "render", "task_id": "abcdef12-99",
                                                                  "title": "Cinder"}}]})
        assert text == '[render abcdef12 "Cinder"]'
