"""Unit tests for subreddit_profiles CRUD (fake pool, no real DB)."""
from __future__ import annotations

import pytest

from services.community_drafts import (
    SubredditProfile,
    add_profile,
    edit_profile,
    get_profile,
    list_profiles,
    remove_profile,
    set_profile_enabled,
)


# --- fake pool doubles (shape mirrors test_affiliate_links_crud.py) ---
class _NullAsyncCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, fetchrow_result=None):
        self.calls = []
        self._fetchrow_result = fetchrow_result

    def transaction(self):
        return _NullAsyncCtx()


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, *, execute_result="UPDATE 1", fetchrow_result=None, fetch_rows=None):
        self.conn = _FakeConn(fetchrow_result=fetchrow_result)
        self._execute_result = execute_result
        self._fetch_rows = fetch_rows or []
        self.fetch_sql = None

    def acquire(self):
        return _AcquireCtx(self.conn)

    async def execute(self, sql, *args):
        self.conn.calls.append(("pool.execute", sql, args))
        return self._execute_result

    async def fetch(self, sql, *args):
        self.fetch_sql = sql
        return self._fetch_rows

    async def fetchrow(self, sql, *args):
        self.conn.calls.append(("pool.fetchrow", sql, args))
        return self.conn._fetchrow_result


def _row(**over):
    base = dict(
        subreddit="LocalLLaMA", enabled=True, content_types=["ai-ml"],
        post_type="text", self_promo="strict", flair=None, min_karma=None,
        min_account_age_days=None, rules_summary="", tone_notes="", cadence_cap_days=None,
    )
    base.update(over)
    return base


async def test_add_profile_returns_true_on_insert():
    pool = _FakePool(execute_result="INSERT 0 1")
    ok = await add_profile(pool, SubredditProfile(subreddit="LocalLLaMA", content_types=["ai-ml"]))
    assert ok is True
    _, sql, args = pool.conn.calls[0]
    assert "ON CONFLICT (subreddit) DO NOTHING" in sql
    assert args[0] == "LocalLLaMA"
    assert args[2] == ["ai-ml"]          # content_types passed as a list (text[])


async def test_add_profile_returns_false_on_conflict():
    pool = _FakePool(execute_result="INSERT 0 0")
    assert await add_profile(pool, SubredditProfile(subreddit="LocalLLaMA")) is False


async def test_list_profiles_enabled_only_filters():
    pool = _FakePool(fetch_rows=[_row()])
    out = await list_profiles(pool, enabled_only=True)
    assert "WHERE enabled" in pool.fetch_sql
    assert out[0].subreddit == "LocalLLaMA"
    assert out[0].content_types == ["ai-ml"]


async def test_list_profiles_all_has_no_filter():
    pool = _FakePool(fetch_rows=[])
    assert await list_profiles(pool) == []
    assert "WHERE enabled" not in pool.fetch_sql


async def test_get_profile_none_when_missing():
    pool = _FakePool(fetchrow_result=None)
    assert await get_profile(pool, "nope") is None


async def test_edit_profile_merges_only_provided_fields():
    pool = _FakePool(fetchrow_result=_row(flair=None, tone_notes="old"))
    merged = await edit_profile(pool, "LocalLLaMA", flair="Discussion", tone_notes=None)
    assert merged.flair == "Discussion"     # provided → applied
    assert merged.tone_notes == "old"       # None → left unchanged
    assert merged.content_types == ["ai-ml"]


async def test_edit_profile_raises_when_missing():
    pool = _FakePool(fetchrow_result=None)
    with pytest.raises(KeyError):
        await edit_profile(pool, "nope", flair="x")


async def test_set_profile_enabled_returns_true_on_match():
    pool = _FakePool(execute_result="UPDATE 1")
    assert await set_profile_enabled(pool, "LocalLLaMA", False) is True


async def test_remove_profile_returns_false_when_no_match():
    pool = _FakePool(execute_result="DELETE 0")
    assert await remove_profile(pool, "nope") is False
