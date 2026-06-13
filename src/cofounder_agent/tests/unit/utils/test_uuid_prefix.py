"""Unit tests for utils/uuid_prefix.py — server-side UUID-prefix resolution.

The HTTP-layer twin of ``poindexter.cli._prefix.resolve_uuid_prefix``: operators
paste 8-char id prefixes (``LEFT(<id>::text, 8)``) from Grafana / ``poindexter
tasks list`` into commands that mutate through the worker API, so the route
handlers must expand them server-side before their exact-match UUID writes.

These tests pin the contract:
  * full-length UUID  → returned unchanged, NO DB round trip
  * one prefix match  → the full id
  * zero matches      → HTTPException(404)
  * many matches      → HTTPException(409)
and that the comparison is done on ``<column>::text LIKE $1 || '%'`` (the only
shape that survives a real ``uuid`` column without a client-side cast).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from utils.uuid_prefix import (
    looks_like_full_uuid,
    resolve_task_id_prefix,
    resolve_uuid_prefix,
)

FULL_UUID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_UUID = "550e8400-e29b-41d4-a716-446655440099"


# ---------------------------------------------------------------------------
# Fake asyncpg pool — minimal, real async-context-manager semantics.
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return list(self._rows)


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


class _FakePool:
    """Returns ``rows`` from every ``conn.fetch`` and counts acquisitions."""

    def __init__(self, rows):
        self.conn = _FakeConn(rows)
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _FakeAcquire(self.conn)


def _rows(*ids):
    # asyncpg Records are mapping-like; plain dicts satisfy ``row["id"]``.
    return [{"id": i} for i in ids]


# ---------------------------------------------------------------------------
# looks_like_full_uuid
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLooksLikeFullUuid:
    def test_full_dashed_uuid_is_true(self):
        assert looks_like_full_uuid(FULL_UUID) is True

    def test_eight_char_prefix_is_false(self):
        assert looks_like_full_uuid("550e8400") is False

    def test_numeric_id_is_false(self):
        assert looks_like_full_uuid("42") is False

    def test_empty_is_false(self):
        assert looks_like_full_uuid("") is False

    def test_undashed_32_char_is_false(self):
        # 32 hex chars, no dashes — wrong dash count, so not "full" shape.
        assert looks_like_full_uuid(FULL_UUID.replace("-", "")) is False


# ---------------------------------------------------------------------------
# resolve_uuid_prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestResolveUuidPrefix:
    async def test_full_uuid_returns_unchanged_without_db(self):
        pool = _FakePool(_rows())  # rows irrelevant — must not be queried
        result = await resolve_uuid_prefix(
            pool, table="posts", column="id", value=FULL_UUID, noun="post"
        )
        assert result == FULL_UUID
        assert pool.acquire_count == 0  # no DB round trip for a full id

    async def test_unique_prefix_resolves_to_full_id(self):
        pool = _FakePool(_rows(FULL_UUID))
        result = await resolve_uuid_prefix(
            pool, table="posts", column="id", value="550e8400", noun="post"
        )
        assert result == FULL_UUID
        assert pool.acquire_count == 1

    async def test_zero_matches_raises_404(self):
        pool = _FakePool(_rows())
        with pytest.raises(HTTPException) as exc:
            await resolve_uuid_prefix(
                pool, table="posts", column="id", value="deadbeef", noun="post"
            )
        assert exc.value.status_code == 404
        assert "deadbeef" in exc.value.detail

    async def test_ambiguous_prefix_raises_409(self):
        pool = _FakePool(_rows(FULL_UUID, OTHER_UUID))
        with pytest.raises(HTTPException) as exc:
            await resolve_uuid_prefix(
                pool, table="posts", column="id", value="550e8400", noun="post"
            )
        assert exc.value.status_code == 409
        # The disambiguation message names both candidates + how to recover.
        assert FULL_UUID in exc.value.detail
        assert OTHER_UUID in exc.value.detail
        assert "longer prefix" in exc.value.detail.lower()

    async def test_compares_on_column_text_like_prefix(self):
        """The bound value must flow into ``<column>::text LIKE $1 || '%'`` —
        the cast-to-text comparison is what keeps a bare prefix off a real
        ``uuid`` column's client-side validation."""
        pool = _FakePool(_rows(FULL_UUID))
        await resolve_uuid_prefix(pool, table="posts", column="id", value="550e8400", noun="post")
        sql, args = pool.conn.fetch_calls[0]
        assert "id::text LIKE $1 || '%'" in sql
        assert "FROM posts" in sql
        assert args == ("550e8400",)

    async def test_non_hex_value_404s_without_db(self):
        """A value carrying non-hex characters can never prefix a hex UUID, so
        it 404s immediately — LIKE-matching ``'nonexistent-task-id'`` against a
        uuid column is guaranteed-empty work that also trips bare mock pools."""
        pool = _FakePool(_rows(FULL_UUID))  # rows present — must NOT be queried
        with pytest.raises(HTTPException) as exc:
            await resolve_uuid_prefix(
                pool,
                table="posts",
                column="id",
                value="nonexistent-task-id",
                noun="post",
            )
        assert exc.value.status_code == 404
        assert "nonexistent-task-id" in exc.value.detail
        assert pool.acquire_count == 0  # short-circuited before any DB round trip


# ---------------------------------------------------------------------------
# resolve_task_id_prefix — task-specific wrapper (numeric + UUID passthrough)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestResolveTaskIdPrefix:
    async def test_full_uuid_passes_through_without_db(self):
        pool = _FakePool(_rows())
        result = await resolve_task_id_prefix(pool, FULL_UUID)
        assert result == FULL_UUID
        assert pool.acquire_count == 0

    async def test_numeric_legacy_id_passes_through_without_db(self):
        # Legacy numeric ids resolve against pipeline_tasks.id downstream in
        # get_task; the prefix resolver must not LIKE-match them.
        pool = _FakePool(_rows())
        result = await resolve_task_id_prefix(pool, "42")
        assert result == "42"
        assert pool.acquire_count == 0

    async def test_prefix_resolves_against_pipeline_tasks_task_id(self):
        pool = _FakePool(_rows(FULL_UUID))
        result = await resolve_task_id_prefix(pool, "550e8400")
        assert result == FULL_UUID
        sql, args = pool.conn.fetch_calls[0]
        assert "FROM pipeline_tasks" in sql
        assert "task_id::text LIKE $1 || '%'" in sql
        assert args == ("550e8400",)

    async def test_ambiguous_prefix_raises_409(self):
        pool = _FakePool(_rows(FULL_UUID, OTHER_UUID))
        with pytest.raises(HTTPException) as exc:
            await resolve_task_id_prefix(pool, "550e8400")
        assert exc.value.status_code == 409

    async def test_zero_matches_raises_404(self):
        pool = _FakePool(_rows())
        with pytest.raises(HTTPException) as exc:
            await resolve_task_id_prefix(pool, "0bc9badd")
        assert exc.value.status_code == 404

    async def test_non_hex_path_value_404s_without_db(self):
        """The live integration path: ``GET /api/tasks/nonexistent-task-id`` must
        404, not 500. A non-hex id can't prefix a real ``pipeline_tasks.task_id``
        UUID, so the wrapper must short-circuit before touching the pool."""
        pool = _FakePool(_rows(FULL_UUID))
        with pytest.raises(HTTPException) as exc:
            await resolve_task_id_prefix(pool, "nonexistent-task-id")
        assert exc.value.status_code == 404
        assert pool.acquire_count == 0
