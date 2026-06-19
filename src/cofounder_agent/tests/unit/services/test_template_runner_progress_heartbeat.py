"""Unit tests for the per-node progress heartbeat (pipeline_tasks.last_progress_at).

The brain's prefect_stuck_flow_probe reads this column; these tests pin the
write side: the shared helper, the extended stage-column write, and the atom
wrapper wiring (Task 3).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from services import template_runner as tr


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> None:
        self.calls.append((query, args))


class _FakePool:
    """asyncpg-pool stand-in exposing acquire() as an async context manager."""

    def __init__(
        self, *, conn: _FakeConn | None = None, raise_on_acquire: bool = False
    ) -> None:
        self.conn = conn or _FakeConn()
        self._raise = raise_on_acquire

    def acquire(self):
        pool = self

        class _CM:
            async def __aenter__(self):
                if pool._raise:
                    raise RuntimeError("pool exhausted")
                return pool.conn

            async def __aexit__(self, *exc):
                return False

        return _CM()


@pytest.mark.asyncio
async def test_mark_progress_updates_last_progress_at():
    pool = _FakePool()
    await tr._mark_progress(pool, "task-abc")
    assert len(pool.conn.calls) == 1
    query, args = pool.conn.calls[0]
    assert "last_progress_at" in query
    assert "pipeline_tasks" in query
    assert args == ("task-abc",)


@pytest.mark.asyncio
async def test_mark_progress_noop_on_none_pool_or_task():
    # No pool → no crash, nothing to assert beyond "returns".
    await tr._mark_progress(None, "task-abc")
    pool = _FakePool()
    await tr._mark_progress(pool, None)
    assert pool.conn.calls == []


@pytest.mark.asyncio
async def test_mark_progress_swallows_db_errors():
    pool = _FakePool(raise_on_acquire=True)
    # Must not raise.
    await tr._mark_progress(pool, "task-abc")


@pytest.mark.asyncio
async def test_mark_stage_column_also_stamps_progress():
    pool = _FakePool()
    await tr._mark_stage_column(pool, "task-xyz", "generate_media_scripts")
    assert len(pool.conn.calls) == 1
    query, args = pool.conn.calls[0]
    assert "stage = $1" in query
    assert "last_progress_at = NOW()" in query
    assert args == ("generate_media_scripts", "task-xyz")


@pytest.mark.asyncio
async def test_claim_pending_task_stamps_progress_at_claim():
    """claim_pending_task must stamp last_progress_at so the column is
    non-NULL from the first moment a task is in_progress."""
    from services.flows import content_generation as cg

    executed: list[tuple[str, tuple[Any, ...]]] = []

    class _Conn:
        async def fetchrow(self, query: str, *args: Any):
            return {
                "task_id": "t1", "topic": "T", "style": "technical",
                "tone": "professional", "target_length": 1500,
                "category": None, "target_audience": None, "niche_slug": None,
                "template_slug": "canonical_blog", "primary_keyword": None,
                "site_id": None,
            }

        async def execute(self, query: str, *args: Any):
            executed.append((query, args))

        def transaction(self):
            class _Tx:
                async def __aenter__(_self):
                    return None

                async def __aexit__(_self, *exc):
                    return False

            return _Tx()

    class _Pool:
        def acquire(self):
            conn = _Conn()

            class _CM:
                async def __aenter__(self):
                    return conn

                async def __aexit__(self, *exc):
                    return False

            return _CM()

    db = MagicMock()
    db.pool = _Pool()

    claimed = await cg.claim_pending_task.fn(db)
    assert claimed is not None
    update_sql = " ".join(q for q, _ in executed)
    assert "last_progress_at" in update_sql


@pytest.mark.asyncio
async def test_atom_node_stamps_progress_on_start():
    """A wrapped atom node must stamp last_progress_at via the heartbeat
    helper, using the pool from the threaded database_service."""
    from services import pipeline_architect as pa

    conn = _FakeConn()
    db = MagicMock()
    db.pool = _FakePool(conn=conn)

    async def _run_fn(atom_input: dict[str, Any]) -> dict[str, Any]:
        return {}

    node = pa._wrap_atom(
        _run_fn, "qa.programmatic", "n1", None,
        node_config=None, on_event=None, index=0, total=1, retry_policy=None,
    )
    state = {"task_id": "task-atom"}
    config = {"configurable": {"__services__": {"database_service": db}}}
    await node(state, config)

    assert any("last_progress_at" in q for q, _ in conn.calls)
    assert any(args == ("task-atom",) for _, args in conn.calls)
