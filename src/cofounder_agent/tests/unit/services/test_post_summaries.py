"""The answer-first "In brief" summary for each published post.

2026-10-18: AI answer engines quote passages that state a finding plainly; the
posts open with a narrative hook and the meta description sells a click. A job
writes two or three answer-first sentences per published main-feed post into
posts.metadata->>'summary', which the static export carries to the site.

Pinned here: the code checks (length, not about itself, no number the post
lacks), one retry carrying the reason, no summary after two failures, the
store/failure writes and newest-first selection, the job's switch and re-export,
and the export fields.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.services import post_summaries as ps
from poindexter.services.site_config import SiteConfig

POST = "The RTX 5090 runs Llama 3.3 70B at 4-bit with 32 GB of VRAM, at 41 tokens per second on llama.cpp."


class TestCheck:
    def test_a_faithful_summary_passes(self):
        assert ps.check_summary("The RTX 5090 runs Llama 3.3 70B at 4-bit at 41 tokens per second.", POST, max_chars=420) == []

    def test_a_number_the_post_lacks_is_caught(self):
        problems = ps.check_summary("The RTX 5090 runs 70B models at 55 tokens per second.", POST, max_chars=420)
        assert problems == ["it has numbers the post does not: 55"]

    @pytest.mark.parametrize("text", ["This article explains VRAM.", "The post covers GPUs.", "In this article we test GPUs."])
    def test_a_summary_about_itself_is_caught(self, text):
        assert "it talks about the article instead of stating what it found" in ps.check_summary(text, POST, max_chars=420)

    def test_too_long_and_empty(self):
        assert ps.check_summary("x " * 300, POST, max_chars=420)[0].startswith("it is 599 characters")
        assert ps.check_summary("  ", POST, max_chars=420) == ["it is empty"]


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(llm_text, "resolve_local_writer_model", lambda **kw: "ollama/local-writer")
    return mock


def _sc(**extra):
    return SiteConfig(initial_config={"post_summary_max_chars": "420", **extra})


class TestSummarize:
    @pytest.mark.asyncio
    async def test_a_good_first_reply_is_kept_unchanged(self, chat):
        chat.return_value = '"The RTX 5090 runs Llama 3.3 70B at 41 tokens per second."'
        out = await ps.summarize_post(title="T", content=POST, site_config=_sc(), pool=None)
        assert out == "The RTX 5090 runs Llama 3.3 70B at 41 tokens per second."
        assert chat.await_count == 1
        assert chat.await_args.kwargs["model"] == "ollama/local-writer"
        assert chat.await_args.kwargs["phase"] == "post_summary"

    @pytest.mark.asyncio
    async def test_a_failed_reply_is_retried_once_with_the_reason(self, chat):
        chat.side_effect = ["The 5090 hits 99 tokens per second.", "The RTX 5090 runs 70B at 41 tokens per second."]
        out = await ps.summarize_post(title="T", content=POST, site_config=_sc(), pool=None)
        assert out == "The RTX 5090 runs 70B at 41 tokens per second."
        assert "numbers the post does not: 99" in chat.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_two_failures_are_no_summary(self, chat):
        chat.return_value = "This article is about GPUs."
        assert await ps.summarize_post(title="T", content=POST, site_config=_sc(), pool=None) is None
        assert chat.await_count == 2

    @pytest.mark.asyncio
    async def test_the_model_setting_wins(self, chat):
        chat.return_value = "The RTX 5090 runs 70B models."
        await ps.summarize_post(title="T", content=POST, site_config=_sc(post_summary_model="anthropic/claude-sonnet-5-5"), pool=None)
        assert chat.await_args.kwargs["model"] == "anthropic/claude-sonnet-5-5"


def _pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()
    pool = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


@pytest.mark.asyncio
async def test_fill_stores_a_summary_and_marks_a_failure(monkeypatch):
    pool, conn = _pool([{"id": 1, "slug": "good", "title": "A", "content": POST},
                        {"id": 2, "slug": "bad", "title": "B", "content": POST}])
    monkeypatch.setattr(ps, "summarize_post", AsyncMock(side_effect=["The 5090 runs 70B.", None]))
    out = await ps.fill_missing_summaries(pool, site_config=_sc(), limit=5)
    assert out == {"written": ["good"], "failed": ["bad"]}
    select = conn.fetch.await_args.args[0]
    assert "dev_diary" in select and "ORDER BY p.published_at DESC" in select and "summary_failed_at" in select
    stored, failed = (c.args for c in conn.execute.await_args_list)
    assert "jsonb_build_object('summary'" in stored[0] and stored[1:] == (1, "The 5090 runs 70B.")
    assert "summary_failed_at" in failed[0] and failed[1:] == (2,)


class TestJob:
    @pytest.mark.asyncio
    async def test_off_by_default(self):
        from poindexter.services.jobs.generate_post_summaries import GeneratePostSummariesJob

        res = await GeneratePostSummariesJob().run(MagicMock(), {"_site_config": _sc()})
        assert res.ok and res.changes_made == 0 and "off" in res.detail

    @pytest.mark.asyncio
    async def test_written_posts_are_re_exported(self, monkeypatch):
        from poindexter.services import static_export_service as se
        from poindexter.services.jobs.generate_post_summaries import GeneratePostSummariesJob

        monkeypatch.setattr(ps, "fill_missing_summaries", AsyncMock(return_value={"written": ["a", "b"], "failed": ["c"]}))
        export = AsyncMock(return_value=True)
        monkeypatch.setattr(se, "export_post", export)
        res = await GeneratePostSummariesJob().run(MagicMock(), {"_site_config": _sc(post_summary_enabled="true"), "limit": 3})
        assert res.changes_made == 2 and res.metrics == {"written": 2, "failed": 1, "exported": 2}
        assert [c.args[1] for c in export.await_args_list] == ["a", "b"]


def test_the_export_carries_the_summary():
    from poindexter.services.static_export_service import _post_summary

    base = {"id": 1, "title": "T", "slug": "s"}
    assert _post_summary({**base, "summary": "The finding."})["summary"] == "The finding."
    assert _post_summary(base)["summary"] is None


def test_pack_prompt_renders():
    from poindexter.services.prompt_manager import get_prompt_manager

    text = get_prompt_manager().get_prompt("seo.post_summary", title="T", content="C", max_chars=420, feedback="")
    assert "under 420 characters" in text and "Title: T" in text and "Reply with the summary only." in text
