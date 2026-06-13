"""Unit tests for the shared CLI uuid-prefix resolver.

``poindexter/cli/_prefix.py`` consolidates the exact-then-LIKE-prefix
logic that #480 (approval), #1490 (pipeline), and the media / publish /
schedule gaps each re-implemented (or crashed for lack of). Operator
surfaces — Grafana panels, ``poindexter tasks list``, ``media pending`` —
render ids as 8-char prefixes (``LEFT(<id>::text, 8)``); operators paste
those back into CLI commands whose service layer exact-matches a UUID
column. asyncpg then rejects the bare prefix client-side, or the query
returns nothing.

These tests pin the resolver's contract so every CLI call site can lean
on it without re-deriving the SQL or the zero/one/many decision.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import click
import pytest

from poindexter.cli._prefix import (
    AmbiguousPrefixError,
    fetch_prefix_candidates,
    looks_like_full_uuid,
    resolve_uuid_prefix,
)

FULL = "6bf91cc3-0281-4b93-aa02-b04ebc1ab45b"


def _pool_returning(rows):
    """asyncpg pool double whose ``.acquire()`` yields a conn whose
    ``.fetch()`` returns the canned row list. Mirrors the double the
    #480 approval tests use so the access pattern stays identical."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    pool._conn = conn  # exposed so tests can assert on the bound SQL/params
    return pool


# ---------------------------------------------------------------------------
# looks_like_full_uuid
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLooksLikeFullUuid:
    def test_full_uuid_true(self):
        assert looks_like_full_uuid(FULL)

    def test_prefix_false(self):
        assert not looks_like_full_uuid("6bf91cc3")

    def test_empty_false(self):
        assert not looks_like_full_uuid("")


# ---------------------------------------------------------------------------
# resolve_uuid_prefix — the high-level resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveUuidPrefix:
    @pytest.mark.asyncio
    async def test_full_uuid_returns_unchanged_no_db(self):
        """A 36-char dashed UUID is returned as-is, no connection acquired."""
        pool = MagicMock()
        assert await resolve_uuid_prefix(pool, table="posts", column="id", prefix=FULL) == FULL
        pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_match_returns_full_id(self):
        pool = _pool_returning([{"id": FULL}])
        out = await resolve_uuid_prefix(pool, table="posts", column="id", prefix="6bf91cc3")
        assert out == FULL

    @pytest.mark.asyncio
    async def test_zero_match_returns_none(self):
        pool = _pool_returning([])
        out = await resolve_uuid_prefix(pool, table="posts", column="id", prefix="deadbeef")
        assert out is None

    @pytest.mark.asyncio
    async def test_many_matches_raises_ambiguous_with_noun(self):
        pool = _pool_returning([
            {"id": "abc11111-1111-1111-1111-111111111111"},
            {"id": "abc22222-2222-2222-2222-222222222222"},
        ])
        with pytest.raises(AmbiguousPrefixError) as exc:
            await resolve_uuid_prefix(pool, table="posts", column="id", prefix="abc", noun="post")
        msg = str(exc.value)
        assert "matches 2 posts" in msg
        assert "abc11111" in msg and "abc22222" in msg
        assert "longer prefix" in msg

    @pytest.mark.asyncio
    async def test_ambiguous_is_a_click_usage_error(self):
        """Uncaught, it renders as a clean Click error (exit 2), not a traceback."""
        pool = _pool_returning([{"id": "a1"}, {"id": "a2"}])
        with pytest.raises(click.UsageError):
            await resolve_uuid_prefix(pool, table="posts", column="id", prefix="a")

    @pytest.mark.asyncio
    async def test_duplicate_ids_collapse_to_single(self):
        """``media_approvals.post_id`` repeats per medium — the same id
        twice is one post, NOT an ambiguous prefix."""
        pool = _pool_returning([{"post_id": FULL}, {"post_id": FULL}])
        out = await resolve_uuid_prefix(
            pool, table="media_approvals", column="post_id", prefix="6bf91cc3",
        )
        assert out == FULL


# ---------------------------------------------------------------------------
# fetch_prefix_candidates — the SQL-shaping primitive
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchPrefixCandidates:
    @pytest.mark.asyncio
    async def test_builds_like_query_with_text_cast_prefix_bound_last(self):
        pool = _pool_returning([{"id": "x"}])
        await fetch_prefix_candidates(pool, table="posts", column="id", prefix="abc")
        sql, *bound = pool._conn.fetch.call_args.args
        assert "id::text LIKE $1 || '%'" in sql
        assert "FROM posts" in sql
        assert bound == ["abc"]  # prefix stays text, bound as the last param

    @pytest.mark.asyncio
    async def test_extra_where_params_precede_the_prefix(self):
        pool = _pool_returning([])
        await fetch_prefix_candidates(
            pool, table="pipeline_tasks", column="task_id", prefix="abc",
            extra_where="status = $1", params=("awaiting_approval",),
        )
        sql, *bound = pool._conn.fetch.call_args.args
        assert "status = $1" in sql
        assert "task_id::text LIKE $2 || '%'" in sql
        assert bound == ["awaiting_approval", "abc"]

    @pytest.mark.asyncio
    async def test_select_extra_order_by_and_limit(self):
        pool = _pool_returning([])
        await fetch_prefix_candidates(
            pool, table="pipeline_tasks", column="task_id", prefix="abc",
            select_extra=("status", "topic"), order_by="created_at DESC", limit=5,
        )
        sql = pool._conn.fetch.call_args.args[0]
        assert "task_id::text AS task_id" in sql
        assert "status, topic" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT 5" in sql
