"""Unit tests for the affiliate-link CRUD helpers (fake pool, no real DB).

Complements test_affiliate_links_service.py (pure matcher tests, no DB at
all) and the integration_db schema guard — these assert the SQL shape +
parameter order add_link/list_active/list_all/set_active/remove_link send,
using capturing fake pool/connection doubles.
"""

from __future__ import annotations

import pytest

from modules.content.affiliate_links import (
    add_link,
    list_active,
    list_all,
    remove_link,
    set_active,
)


class _NullAsyncCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, fetchval_result=1):
        self.calls: list[tuple] = []
        self._fetchval_result = fetchval_result

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._fetchval_result

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "INSERT 0 1"

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
    def __init__(self, fetchval_result=1, execute_result: str = "UPDATE 1"):
        self.conn = _FakeConn(fetchval_result=fetchval_result)
        self._execute_result = execute_result

    def acquire(self):
        return _AcquireCtx(self.conn)

    async def execute(self, sql, *args):
        self.conn.calls.append(("pool.execute", sql, args))
        return self._execute_result


async def test_add_link_persists_description_category_platform_and_keywords():
    pool = _FakePool(fetchval_result=42)
    await add_link(
        pool, code="mercury", keywords=["Mercury", "Mercury Bank"],
        url="https://mercury.com/r/glad-labs", display_text="Mercury",
        program="Mercury Referral", description="Business banking we use daily.",
        category="service", platform="direct",
    )
    calls = pool.conn.calls
    kind, sql, args = calls[0]
    assert kind == "fetchval"
    assert "platform" in sql
    assert "keyword" not in sql  # keyword rows are separate inserts, not this row
    assert args == (
        "mercury", "https://mercury.com/r/glad-labs", "Mercury",
        "Mercury Referral", "Business banking we use daily.", "service", "direct",
    )
    delete_kind, delete_sql, delete_args = calls[1]
    assert delete_kind == "execute"
    assert "DELETE FROM affiliate_link_keywords" in delete_sql
    assert delete_args == (42,)
    kw_calls = calls[2:]
    assert [c[2] for c in kw_calls] == [(42, "Mercury"), (42, "Mercury Bank")]


async def test_add_link_dedupes_repeated_keywords_preserving_order():
    pool = _FakePool(fetchval_result=7)
    await add_link(pool, code="x", keywords=["A", "A", "B"], url="https://x")
    kw_calls = pool.conn.calls[2:]
    assert [c[2] for c in kw_calls] == [(7, "A"), (7, "B")]


async def test_add_link_requires_at_least_one_keyword():
    pool = _FakePool()
    with pytest.raises(ValueError):
        await add_link(pool, code="x", keywords=[], url="https://x")


async def test_add_link_defaults_category_to_product():
    pool = _FakePool(fetchval_result=1)
    await add_link(
        pool, code="widget", keywords=["Widget"], url="https://x",
        description="A thing.",
    )
    _, _, args = pool.conn.calls[0]
    assert args[-2] == "product"  # (..., category, platform) — category is 2nd-to-last


class _ListPool:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_sql = None

    async def fetch(self, sql):
        self.fetch_sql = sql
        return self._rows


async def test_list_active_aggregates_keywords_and_platform():
    pool = _ListPool([{
        "code": "mercury", "url": "https://x", "display_text": "Mercury",
        "platform": "direct", "keywords": ["Mercury", "Mercury Bank"],
    }])
    links = await list_active(pool)
    assert "affiliate_link_keywords" in pool.fetch_sql
    assert "is_active = true" in pool.fetch_sql
    assert len(links) == 1
    assert links[0].code == "mercury"
    assert links[0].keywords == ["Mercury", "Mercury Bank"]
    assert links[0].platform == "direct"


async def test_list_all_has_no_active_filter():
    pool = _ListPool([])
    assert await list_all(pool) == []
    assert "is_active" not in pool.fetch_sql


async def test_set_active_returns_true_on_match():
    pool = _FakePool(execute_result="UPDATE 1")
    assert await set_active(pool, "mercury", False) is True


async def test_set_active_returns_false_when_no_match():
    pool = _FakePool(execute_result="UPDATE 0")
    assert await set_active(pool, "nope", False) is False


async def test_remove_link_returns_true_on_match():
    pool = _FakePool(execute_result="DELETE 1")
    assert await remove_link(pool, "mercury") is True


async def test_remove_link_returns_false_when_no_match():
    pool = _FakePool(execute_result="DELETE 0")
    assert await remove_link(pool, "nope") is False
