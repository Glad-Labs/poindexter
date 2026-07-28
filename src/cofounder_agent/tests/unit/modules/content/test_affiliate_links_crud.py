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


# --- add_keywords / remove_keywords (poindexter#931) -------------------------
# add_link replaces a link's whole keyword set, so introducing one alias with
# it silently drops the rest. These are the additive path.


class _QueuedConn(_FakeConn):
    """Fake conn whose fetchval returns a queued sequence of values."""

    def __init__(self, fetchvals):
        super().__init__()
        self._queue = list(fetchvals)

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self._queue.pop(0) if self._queue else None


class _QueuedPool(_FakePool):
    def __init__(self, fetchvals, execute_result="INSERT 0 1"):
        super().__init__()
        self.conn = _QueuedConn(fetchvals)
        self._execute_result = execute_result

    def acquire(self):
        return _AcquireCtx(self.conn)


@pytest.mark.asyncio
async def test_add_keywords_appends_and_counts_new_rows():
    from modules.content.affiliate_links import add_keywords

    pool = _QueuedPool([7])  # link_id lookup
    added = await add_keywords(pool, code="mercury", keywords=["Mercury Bank"])
    assert added == 1
    inserts = [c for c in pool.conn.calls
               if c[0] == "execute" and "INSERT INTO affiliate_link_keywords" in c[1]]
    assert len(inserts) == 1
    # must not clobber the existing set the way add_link does
    assert not any("DELETE FROM affiliate_link_keywords" in c[1]
                   for c in pool.conn.calls if c[0] == "execute")
    assert "ON CONFLICT" in inserts[0][1]


@pytest.mark.asyncio
async def test_add_keywords_dedupes_and_strips_input():
    from modules.content.affiliate_links import add_keywords

    pool = _QueuedPool([7])
    await add_keywords(pool, code="mercury", keywords=[" Mercury ", "Mercury", ""])
    inserts = [c for c in pool.conn.calls
               if c[0] == "execute" and "INSERT INTO affiliate_link_keywords" in c[1]]
    assert len(inserts) == 1
    assert inserts[0][2][1] == "Mercury"


@pytest.mark.asyncio
async def test_add_keywords_unknown_code_fails_loud():
    from modules.content.affiliate_links import add_keywords

    pool = _QueuedPool([None])  # no link_id
    with pytest.raises(LookupError, match="nope"):
        await add_keywords(pool, code="nope", keywords=["X"])


@pytest.mark.asyncio
async def test_add_keywords_rejects_empty_input():
    from modules.content.affiliate_links import add_keywords

    with pytest.raises(ValueError):
        await add_keywords(_QueuedPool([7]), code="mercury", keywords=["  "])


@pytest.mark.asyncio
async def test_remove_keywords_deletes_and_counts():
    from modules.content.affiliate_links import remove_keywords

    # link_id=7, total=3 keywords, 1 of them doomed
    pool = _QueuedPool([7, 3, 1], execute_result="DELETE 1")
    pool.conn._execute_result = "DELETE 1"

    async def _execute(sql, *args):
        pool.conn.calls.append(("execute", sql, args))
        return "DELETE 1"

    pool.conn.execute = _execute
    removed = await remove_keywords(pool, code="mercury", keywords=["Old Alias"])
    assert removed == 1


@pytest.mark.asyncio
async def test_remove_keywords_refuses_to_strip_the_last_one():
    from modules.content.affiliate_links import remove_keywords

    # total=1, doomed=1 -> removing it would make the link uninjectable
    pool = _QueuedPool([7, 1, 1])
    with pytest.raises(ValueError, match="last keyword"):
        await remove_keywords(pool, code="audible", keywords=["Audible"])


@pytest.mark.asyncio
async def test_remove_keywords_unknown_code_fails_loud():
    from modules.content.affiliate_links import remove_keywords

    pool = _QueuedPool([None])
    with pytest.raises(LookupError):
        await remove_keywords(pool, code="nope", keywords=["X"])
