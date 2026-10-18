"""youtube_chapters: the director model proposes, code holds it to YouTube's rules.

A chapter list YouTube ignores (no 0:00), or one that starts a chapter at a
time nobody speaks, is worse than none. These pin the checks that prevent
both, and that chapters ride into the description and never block an upload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import youtube_chapters as ch
from poindexter.services.site_config import SiteConfig

SRT = """1
00:00:00,000 --> 00:00:04,700
In the heart of building a RAG pipeline lies a challenge.

2
00:00:04,740 --> 00:00:30,000
Most embedding models treat every input identically.

3
00:00:31,500 --> 00:00:60,000
Symmetric and asymmetric encoding differ in 2 ways.

4
00:01:05,200 --> 00:01:40,000
Gemini adds task specific parameters.

5
00:01:44,000 --> 00:02:20,000
Evaluate models before you change the chunking.
"""
CUES = ch.parse_srt(SRT)
LIMITS = {"max_chapters": 8, "min_seconds": 20, "max_title_chars": 60}


class TestParsing:
    def test_parse_srt(self):
        assert [round(s, 2) for s, _ in CUES] == [0.0, 4.74, 31.5, 65.2, 104.0]
        assert CUES[1][1] == "Most embedding models treat every input identically."

    @pytest.mark.parametrize("secs, stamp", [(0, "0:00"), (65.9, "1:05"), (3725, "1:02:05")])
    def test_format_stamp(self, secs, stamp):
        assert ch.format_stamp(secs) == stamp


class TestCheck:
    def test_a_good_list_is_snapped_to_cue_starts(self):
        text, why = ch.check_chapters(
            "0:00 Why RAG retrieval misses\n0:33 Symmetric vs asymmetric encoding\n1:06 Gemini task parameters\n",
            CUES, **LIMITS,
        )
        assert why == ""
        assert text.splitlines() == [
            "0:00 Why RAG retrieval misses",
            "0:31 Symmetric vs asymmetric encoding",  # snapped back to the cue at 31.5 s
            "1:05 Gemini task parameters",
        ]

    def test_the_first_chapter_is_forced_to_zero(self):
        text, _ = ch.check_chapters("0:03 Intro to RAG\n0:31 Encoding\n1:05 Gemini", CUES, **LIMITS)
        assert text.splitlines()[0] == "0:00 Intro to RAG"

    def test_bullets_and_separators_are_tolerated(self):
        text, why = ch.check_chapters("- 0:00 - Intro to RAG\n* 0:31: Encoding\n• 1:05 | Gemini", CUES, **LIMITS)
        assert why == "" and len(text.splitlines()) == 3

    @pytest.mark.parametrize("raw, reason", [
        ("0:00 Intro\n0:31 Encoding", "fewer than 3"),
        ("0:00 A\n0:31 B\n1:05 C\n1:44 D", ""),  # 4 is fine
        ("0:00 Intro\n0:05 Too close\n1:05 Gemini", "fewer than 3 left"),
        ("0:00 Intro\n0:31 " + "x" * 61 + "\n1:05 Gemini", "over 60 characters"),
        ("0:00 Chapter 1\n0:31 Encoding\n1:05 Gemini", "a label, not a topic"),
        ("0:00 Intro\n0:31 The 7 encoding modes\n1:05 Gemini", "number not in the transcript"),
        ("0:00 Intro\n0:31 The 2 encoding ways\n1:05 Gemini", ""),  # 2 is in the transcript
    ])
    def test_rejections(self, raw, reason):
        text, why = ch.check_chapters(raw, CUES, **LIMITS)
        if reason:
            assert text == "" and reason in why
        else:
            assert text and why == ""

    def test_too_many(self):
        raw = "\n".join(f"{m}:00 Part about thing {chr(65 + m)}" for m in range(5))
        text, why = ch.check_chapters(raw, CUES, max_chapters=3, min_seconds=10, max_title_chars=60)
        assert text == "" and "more than 3" in why


class TestGenerate:
    async def test_off_and_no_model_and_short_video(self):
        assert (await ch.generate_chapters(
            site_config=SiteConfig(initial_config={"youtube_chapters_enabled": "false"}), pool=object(),
            srt_text=SRT, title="t",
        ))[0] == ""
        assert "no youtube_chapters_model" in (await ch.generate_chapters(
            site_config=SiteConfig(initial_config={}), pool=object(), srt_text=SRT, title="t",
        ))[1]
        short = "1\n00:00:00,000 --> 00:00:05,000\nHi\n"
        assert (await ch.generate_chapters(
            site_config=SiteConfig(initial_config={"video_director_model": "m"}), pool=object(),
            srt_text=short, title="t",
        ))[1] == "video too short for chapters"

    async def test_the_model_reply_goes_through_the_check(self):
        captured = {}

        async def _fake(**kw):
            captured.update(kw)
            return kw["check"]("0:00 Intro to RAG\n0:31 Encoding\n1:05 Gemini parameters")

        with patch("poindexter.services.checked_llm_line.generate_checked_line", _fake):
            text, note = await ch.generate_chapters(
                site_config=SiteConfig(initial_config={"video_director_model": "m"}), pool=object(),
                srt_text=SRT, title="The Gap", key_terms="TileLang, Gemini",
            )
        assert text.startswith("0:00 Intro to RAG") and note == ""
        assert captured["model"] == "m" and captured["phase"] == "youtube_chapters"
        assert "[31] Symmetric" in captured["prompt"]
        assert "TileLang, Gemini" in captured["prompt"]


def _pool(fetchval=None, rows=None):
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.execute = AsyncMock()
    pool = MagicMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acq()
    return pool, conn


class TestForUpload:
    async def test_stored_chapters_are_reused(self):
        pool, _ = _pool(fetchval="0:00 A\n0:40 B\n1:20 C")
        with patch("poindexter.services.youtube_captions.ensure_srt") as ensure:
            text = await ch.chapters_for_upload(pool, SiteConfig(initial_config={}), asset_id="a", video_path="/v.mp4", title="t")
        assert text.startswith("0:00 A")
        ensure.assert_not_called()

    async def test_made_stored_and_returned(self, tmp_path):
        srt = tmp_path / "v.srt"
        srt.write_text(SRT)
        pool, conn = _pool(fetchval=None)
        with patch("poindexter.services.youtube_captions.ensure_srt", AsyncMock(return_value=(str(srt), ""))), \
             patch.object(ch, "generate_chapters", AsyncMock(return_value=("0:00 A\n0:31 B\n1:05 C", ""))):
            text = await ch.chapters_for_upload(
                pool, SiteConfig(initial_config={}), asset_id="00000000-0000-0000-0000-000000000001",
                video_path="/v.mp4", title="t",
            )
        assert text == "0:00 A\n0:31 B\n1:05 C"
        assert conn.execute.await_args.args[2] == "youtube_chapters"

    async def test_never_raises(self):
        pool = MagicMock()
        pool.acquire = MagicMock(side_effect=RuntimeError("db down"))
        assert await ch.chapters_for_upload(pool, SiteConfig(initial_config={}), asset_id="a", video_path="/v", title="t") == ""


class TestBackfill:
    async def test_kept_stored_and_failed(self, tmp_path):
        srt = tmp_path / "v.srt"
        srt.write_text(SRT)
        rows = [
            {"id": "00000000-0000-0000-0000-000000000001", "storage_path": "/a.mp4", "task_id": "t1",
             "video_id": "A", "chapters": "0:00 kept", "title": "A"},
            {"id": "00000000-0000-0000-0000-000000000002", "storage_path": "/b.mp4", "task_id": "t2",
             "video_id": "B", "chapters": None, "title": "B"},
            {"id": "00000000-0000-0000-0000-000000000003", "storage_path": "/gone.mp4", "task_id": "t3",
             "video_id": "C", "chapters": None, "title": "C"},
        ]
        pool, _ = _pool(rows=rows)

        async def _ensure(path, sc, task_id=""):
            return ("", "video file missing") if path == "/gone.mp4" else (str(srt), "")

        with patch("poindexter.services.youtube_captions.ensure_srt", _ensure), \
             patch.object(ch, "generate_chapters", AsyncMock(return_value=("0:00 X\n0:31 Y\n1:05 Z", ""))):
            a, b, c = await ch.backfill_chapters(pool, SiteConfig(initial_config={}))
        assert a.action.startswith("kept")
        assert b.action.startswith("stored") and b.chapters.startswith("0:00 X")
        assert c.failed and "missing" in c.action


class TestDescription:
    def _desc(self, **kw):
        from poindexter.services.jobs.youtube_payload import _build_youtube_description

        base = {"seo_description": "Excerpt.", "body": "", "site_config": SiteConfig(initial_config={"site_url": "https://x.io"}), "slug": "s"}
        base.update(kw)
        return _build_youtube_description(**base)

    def test_a_long_video_carries_its_chapters(self):
        desc = self._desc(chapters="0:00 A\n0:31 B\n1:05 C")
        assert "\n\nChapters\n0:00 A\n0:31 B\n1:05 C" in desc

    def test_a_short_never_does(self):
        assert "Chapters" not in self._desc(shorts=True, chapters="0:00 A\n0:31 B\n1:05 C")

    def test_no_chapters_no_block(self):
        assert "Chapters" not in self._desc()


def test_the_sync_composes_stored_chapters():
    from poindexter.services.youtube_metadata_sync import _compose

    row = {"medium": "video", "title": "T", "excerpt": "E", "content": "", "slug": "s",
           "seo_keywords": "", "chapters": "0:00 A\n0:31 B\n1:05 C"}
    _title, desc, _tags = _compose(row, SiteConfig(initial_config={"site_url": "https://x.io"}))
    assert "Chapters\n0:00 A" in desc


def test_the_chapter_prompts_render():
    from poindexter.services.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()
    text = pm.get_prompt(
        "video.chapters", title="T", transcript="[0] hi", key_terms="tilelang, ascend",
        max_chapters=8, min_seconds=20, max_title_chars=60,
    )
    assert "tilelang, ascend" in text
    assert "[0] hi" in text and "0:00" in text
    assert "FIX: r." in pm.get_prompt("video.chapters_fix", reason="r", max_chapters=8, min_seconds=20, max_title_chars=60)


def test_a_crowded_chapter_is_dropped_when_three_remain():
    """2026-10-18: four videos lost every chapter to one crowded last line."""
    text, why = ch.check_chapters(
        "0:00 Intro to RAG\n0:31 Encoding\n1:05 Gemini parameters\n1:15 Too soon after Gemini\n1:44 Evaluating models",
        CUES, **LIMITS,
    )
    assert why == ""
    assert text.splitlines() == [
        "0:00 Intro to RAG", "0:31 Encoding", "1:05 Gemini parameters", "1:44 Evaluating models",
    ]
