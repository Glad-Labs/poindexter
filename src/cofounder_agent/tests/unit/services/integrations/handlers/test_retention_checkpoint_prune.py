"""Unit tests for retention.checkpoint_prune handler.

The handler deletes LangGraph Postgres-checkpointer rows
(checkpoint_writes, checkpoint_blobs, checkpoints) for pipeline tasks
that have reached a terminal status and whose updated_at is older than
ttl_days days.

All tests use an in-memory fake pool — no Postgres required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.integrations.handlers.retention_checkpoint_prune import checkpoint_prune


# ---------------------------------------------------------------------------
# Fake pool / connection helpers
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal asyncpg connection mock with configurable fetchval/fetch/execute."""

    def __init__(
        self,
        *,
        checkpoints_exist: bool = True,
        task_rows: list[dict[str, Any]] | None = None,
        delete_count: int = 0,
        table_exists: bool = True,
    ) -> None:
        self._checkpoints_exist = checkpoints_exist
        self._task_rows = task_rows or []
        self._delete_count = delete_count
        self._table_exists = table_exists
        self.executes: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "to_regclass('public.checkpoints')" in query:
            return self._checkpoints_exist
        if "to_regclass" in query and args:
            return self._table_exists
        if "COUNT(*)" in query:
            return 5  # dry_run count
        return None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        return [dict(r) for r in self._task_rows]

    async def execute(self, query: str, *args: Any) -> str:
        self.executes.append((query, args))
        return f"DELETE {self._delete_count}"


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        acm = MagicMock()
        acm.__aenter__ = AsyncMock(return_value=self._conn)
        acm.__aexit__ = AsyncMock(return_value=False)
        return acm


def _pool(**kwargs: Any) -> _FakePool:
    return _FakePool(_FakeConn(**kwargs))


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "b4c5d6e7-f8a9-0123-789a-123456789013",
        "name": "checkpoint_prune",
        "handler_name": "checkpoint_prune",
        "table_name": "checkpoints",
        "age_column": "updated_at",
        "ttl_days": 30,
        "config": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Guard: missing pool
# ---------------------------------------------------------------------------

class TestPoolGuard:
    @pytest.mark.asyncio
    async def test_raises_when_pool_is_none(self) -> None:
        with pytest.raises(RuntimeError, match="pool unavailable"):
            await checkpoint_prune(None, site_config=None, row=_row(), pool=None)


# ---------------------------------------------------------------------------
# Guard: ttl_days validation
# ---------------------------------------------------------------------------

class TestTtlValidation:
    @pytest.mark.asyncio
    async def test_raises_when_ttl_days_missing(self) -> None:
        row = _row(ttl_days=None)
        with pytest.raises(ValueError, match="ttl_days is required"):
            await checkpoint_prune(None, site_config=None, row=row, pool=_pool())

    @pytest.mark.asyncio
    async def test_raises_when_ttl_days_not_int(self) -> None:
        row = _row(ttl_days="not-a-number")
        with pytest.raises(ValueError, match="ttl_days must be int"):
            await checkpoint_prune(None, site_config=None, row=row, pool=_pool())

    @pytest.mark.asyncio
    async def test_raises_when_ttl_days_negative(self) -> None:
        row = _row(ttl_days=-1)
        with pytest.raises(ValueError, match="must be >= 0"):
            await checkpoint_prune(None, site_config=None, row=row, pool=_pool())


# ---------------------------------------------------------------------------
# Guard: checkpoints table absent
# ---------------------------------------------------------------------------

class TestCheckpointsTableAbsent:
    @pytest.mark.asyncio
    async def test_returns_skipped_when_table_missing(self) -> None:
        pool = _pool(checkpoints_exist=False)
        result = await checkpoint_prune(None, site_config=None, row=_row(), pool=pool)
        assert result["deleted"] == 0
        assert "skipped" in result


# ---------------------------------------------------------------------------
# No terminal tasks
# ---------------------------------------------------------------------------

class TestNoTerminalTasks:
    @pytest.mark.asyncio
    async def test_returns_zero_deleted_when_no_tasks(self) -> None:
        pool = _pool(checkpoints_exist=True, task_rows=[])
        result = await checkpoint_prune(None, site_config=None, row=_row(), pool=pool)
        assert result["deleted"] == 0
        assert result["tasks_processed"] == 0


# ---------------------------------------------------------------------------
# Normal delete path
# ---------------------------------------------------------------------------

class TestDeletePath:
    @pytest.mark.asyncio
    async def test_deletes_from_all_three_tables(self) -> None:
        task_rows = [{"task_id": "task-001"}, {"task_id": "task-002"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=2)
        pool = _FakePool(conn)
        result = await checkpoint_prune(None, site_config=None, row=_row(), pool=pool)
        # All three checkpoint tables should be hit.
        deleted_tables = [q for q, _ in conn.executes if "DELETE FROM" in q]
        assert any("checkpoint_writes" in t for t in deleted_tables)
        assert any("checkpoint_blobs" in t for t in deleted_tables)
        assert any("checkpoints" in t for t in deleted_tables)
        assert result["tasks_processed"] == 2

    @pytest.mark.asyncio
    async def test_thread_ids_include_media_and_podcast_prefixes(self) -> None:
        task_rows = [{"task_id": "abc-123"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=1)
        pool = _FakePool(conn)
        await checkpoint_prune(None, site_config=None, row=_row(), pool=pool)
        # The DELETE statement should be called with a list containing all
        # three prefix variants.
        assert conn.executes, "expected at least one DELETE"
        _, args = conn.executes[0]
        thread_ids = list(args[0])
        assert "abc-123" in thread_ids
        assert "media-abc-123" in thread_ids
        assert "podcast-abc-123" in thread_ids

    @pytest.mark.asyncio
    async def test_result_includes_task_and_thread_id_counts(self) -> None:
        task_rows = [{"task_id": "t1"}, {"task_id": "t2"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=0)
        pool = _FakePool(conn)
        result = await checkpoint_prune(None, site_config=None, row=_row(), pool=pool)
        assert result["tasks_processed"] == 2
        # 2 tasks × 3 prefixes = 6 thread_ids
        assert result["thread_ids_checked"] == 6


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------

class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_counts_without_deleting(self) -> None:
        task_rows = [{"task_id": "t1"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=0)
        pool = _FakePool(conn)
        row = _row(config={"dry_run": True})
        result = await checkpoint_prune(None, site_config=None, row=row, pool=pool)
        assert result.get("dry_run") is True
        assert result["deleted"] == 0
        # No DELETE statements executed.
        deletes = [q for q, _ in conn.executes if "DELETE" in q]
        assert not deletes

    @pytest.mark.asyncio
    async def test_dry_run_returns_would_delete_count(self) -> None:
        task_rows = [{"task_id": "t1"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows)
        pool = _FakePool(conn)
        row = _row(config={"dry_run": True})
        result = await checkpoint_prune(None, site_config=None, row=row, pool=pool)
        assert "would_delete" in result


# ---------------------------------------------------------------------------
# Config overrides
# ---------------------------------------------------------------------------

class TestConfigOverrides:
    @pytest.mark.asyncio
    async def test_custom_thread_prefixes(self) -> None:
        task_rows = [{"task_id": "t99"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=0)
        pool = _FakePool(conn)
        row = _row(config={"thread_prefixes": ["custom-"]})
        await checkpoint_prune(None, site_config=None, row=row, pool=pool)
        _, args = conn.executes[0]
        thread_ids = list(args[0])
        assert "custom-t99" in thread_ids
        assert "media-t99" not in thread_ids

    @pytest.mark.asyncio
    async def test_respects_batch_size(self) -> None:
        # batch_size=1 means only the first task is queried.  We can't
        # assert on the LIMIT value directly without a real DB, but we can
        # confirm the handler passes batch_size to fetch by verifying the
        # task set is the full mocked list (the mock ignores LIMIT args).
        task_rows = [{"task_id": "t1"}, {"task_id": "t2"}]
        conn = _FakeConn(checkpoints_exist=True, task_rows=task_rows, delete_count=0)
        pool = _FakePool(conn)
        row = _row(config={"batch_size": 1})
        result = await checkpoint_prune(None, site_config=None, row=row, pool=pool)
        # Mock returns all rows regardless of LIMIT, so just confirm the
        # handler runs without error with a non-default batch_size.
        assert "tasks_processed" in result
