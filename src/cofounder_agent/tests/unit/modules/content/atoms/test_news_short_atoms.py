"""news_short.* atoms: adapt graph state to services.news_shorts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.modules.content.atoms import (
    news_short_finalize,
    news_short_research,
    news_short_write_script,
)

STORY = {"pool_id": "p1", "source": "rss", "title": "Cards vanish", "url": "https://e.com/s",
         "summary": "", "score": 1.0}


def _state(**kw):
    return {"task_id": "t1", "database_service": SimpleNamespace(pool="POOL"),
            "site_config": "SC", "niche_slug": "starter-blog", **kw}


@pytest.mark.asyncio
async def test_research_puts_the_page_on_content_and_keeps_the_story():
    found = {"source_text": "page text", "related_post": {"slug": "s"}, "subject": "AI"}
    with patch("poindexter.services.news_shorts.research", AsyncMock(return_value=found)) as research:
        out = await news_short_research.run(_state(news_short=STORY))
    assert out["title"] == "Cards vanish"
    assert out["content"] == "page text"
    assert out["news_short"]["url"] == "https://e.com/s"
    assert out["news_short"]["related_post"] == {"slug": "s"}
    assert research.call_args.args[0] == "POOL"
    assert research.call_args.kwargs == {"niche": "starter-blog"}


@pytest.mark.asyncio
async def test_research_without_a_story_fails_loud():
    with pytest.raises(ValueError, match="no news_short story"):
        await news_short_research.run(_state(news_short={}))


@pytest.mark.asyncio
async def test_write_script_produces_the_short_script_and_no_long_one():
    story = {**STORY, "source_text": "page", "subject": "AI"}
    with patch("poindexter.services.news_shorts.write_script",
               AsyncMock(return_value=("the script", ""))) as write:
        out = await news_short_write_script.run(_state(news_short=story))
    assert out == {"short_summary_script": "the script", "podcast_script": ""}
    assert write.call_args.kwargs["subject"] == "AI"
    assert write.call_args.kwargs["source_text"] == "page"


@pytest.mark.asyncio
async def test_a_script_that_fails_its_checks_fails_the_task():
    story = {**STORY, "source_text": "page"}
    with patch("poindexter.services.news_shorts.write_script",
               AsyncMock(return_value=("", "rejected twice (numbers)"))):
        with pytest.raises(ValueError, match="rejected twice"):
            await news_short_write_script.run(_state(news_short=story))


@pytest.mark.asyncio
async def test_write_script_needs_research_first():
    with pytest.raises(ValueError, match="news_short.research first"):
        await news_short_write_script.run(_state(news_short=STORY))


@pytest.mark.asyncio
async def test_finalize_releases_the_short_and_drops_the_page_text():
    story = {**STORY, "source_text": "page", "related_post": {"slug": "s"}}
    with patch("poindexter.services.news_shorts.mark_standalone",
               AsyncMock(return_value=["a1"])) as mark:
        out = await news_short_finalize.run(_state(news_short=story, short_summary_script="sc"))
    assert out["news_short"]["released_asset_ids"] == ["a1"]
    assert "source_text" not in out["news_short"]
    assert mark.call_args.kwargs == {"related": {"slug": "s"}, "script": "sc"}


@pytest.mark.asyncio
async def test_finalize_fails_when_nothing_was_rendered():
    with patch("poindexter.services.news_shorts.mark_standalone", AsyncMock(return_value=[])):
        with pytest.raises(RuntimeError, match="no rendered video_short"):
            await news_short_finalize.run(_state(news_short=STORY))
