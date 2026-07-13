"""Unit tests for content-type match + draft CRUD (fake pool)."""
from __future__ import annotations

from services.community_drafts import (
    create_draft,
    discard_draft,
    edit_draft,
    get_draft,
    list_drafts,
    mark_posted,
    suggest_subreddits_for_post,
)


class _FetchPool:
    def __init__(self, rows=None, fetchval=1, fetchrow=None):
        self.rows = rows or []
        self._fetchval = fetchval
        self._fetchrow = fetchrow
        self.fetch_sql = None
        self.exec_calls = []

    async def fetch(self, sql, *args):
        self.fetch_sql = (sql, args)
        return self.rows

    async def fetchval(self, sql, *args):
        self.exec_calls.append((sql, args))
        return self._fetchval

    async def fetchrow(self, sql, *args):
        return self._fetchrow

    async def execute(self, sql, *args):
        self.exec_calls.append((sql, args))
        return "UPDATE 1"


def _draft_row(**over):
    base = dict(
        id=5, target="reddit:LocalLLaMA", title="T", body="B", post_type="text",
        source_post_id=None, warnings=["set flair: Discussion"], status="draft",
        posted_url=None, model="gemma",
    )
    base.update(over)
    return base


async def test_suggest_uses_array_overlap():
    pool = _FetchPool(rows=[{"subreddit": "LocalLLaMA"}, {"subreddit": "selfhosted"}])
    out = await suggest_subreddits_for_post(pool, "post-uuid")
    sql, args = pool.fetch_sql
    assert "content_types &&" in sql
    assert "post_content_types" in sql
    assert args == ("post-uuid",)
    assert out == ["LocalLLaMA", "selfhosted"]


async def test_create_draft_returns_id_and_defaults_warnings():
    pool = _FetchPool(fetchval=99)
    new_id = await create_draft(pool, target="reddit:X", body="hello")
    assert new_id == 99
    sql, args = pool.exec_calls[0]
    assert "INSERT INTO community_post_drafts" in sql
    assert args[5] == []          # warnings defaults to [] not None (text[] NOT NULL)


async def test_list_drafts_status_filter():
    pool = _FetchPool(rows=[_draft_row()])
    out = await list_drafts(pool, status="draft")
    sql, args = pool.fetch_sql
    assert "WHERE status" in sql and args == ("draft",)
    assert out[0].id == 5 and out[0].warnings == ["set flair: Discussion"]


async def test_get_draft_none_when_missing():
    pool = _FetchPool(fetchrow=None)
    assert await get_draft(pool, 123) is None


async def test_mark_posted_sets_status_and_url():
    pool = _FetchPool()
    assert await mark_posted(pool, 5, url="https://reddit.com/x") is True
    sql, args = pool.exec_calls[0]
    assert "status='posted'" in sql.replace(" ", "") or "status = 'posted'" in sql
    assert args == (5, "https://reddit.com/x")


async def test_discard_sets_status():
    pool = _FetchPool()
    assert await discard_draft(pool, 5) is True
    sql, _ = pool.exec_calls[0]
    assert "discarded" in sql


async def test_edit_draft_no_fields_returns_false():
    pool = _FetchPool()
    assert await edit_draft(pool, 5) is False
