"""Unit tests for ``PluginScheduler._record_last_run`` telemetry write.

Lives at the unit layer because the integration harness's session-scope
fixture machinery currently can't run async-fixture-requiring tests
(pre-existing ``ScopeMismatch`` between ``migrations_applied`` and
``_function_scoped_runner``). These tests stub the pool directly so they
need neither real Postgres nor the broken fixture chain.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.scheduler import PluginScheduler

pytestmark = pytest.mark.asyncio


class _FakePoolCtx:
    """Async context manager that returns a connection mock with an awaitable execute()."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _pool_with_conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))
    return pool, conn


async def test_record_last_run_writes_two_rows_on_success():
    """Each fire writes BOTH the epoch row and the status='ok' row."""
    pool, conn = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    await scheduler._record_last_run("backfill_podcasts", ok=True)

    assert conn.execute.await_count == 2
    args_run = conn.execute.await_args_list[0].args
    args_status = conn.execute.await_args_list[1].args
    assert args_run[1] == "plugin_job_last_run_backfill_podcasts"
    # epoch is a stringified int — sanity-check it parses
    assert int(args_run[2]) > 0
    assert args_status[1] == "plugin_job_last_status_backfill_podcasts"
    assert args_status[2] == "ok"


async def test_record_last_run_marks_err_on_failure():
    """``ok=False`` writes 'err' to the status row (not 'ok')."""
    pool, conn = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    await scheduler._record_last_run("backfill_videos", ok=False)

    status_args = conn.execute.await_args_list[1].args
    assert status_args[1] == "plugin_job_last_status_backfill_videos"
    assert status_args[2] == "err"


async def test_record_last_run_swallows_db_errors():
    """A broken pool must never crash the scheduler loop.

    Telemetry is observability-only; if the DB write fails the next job
    fire still has to run.
    """
    class _BrokenPool:
        def acquire(self):
            raise RuntimeError("pool is dead")

    scheduler = PluginScheduler(_BrokenPool())
    await scheduler._record_last_run("anything", ok=True)  # must NOT raise


def test_get_stats_returns_safe_defaults_before_any_jobs_fire():
    """Fresh scheduler exposes the contract used by /api/metrics/operational
    (poindexter#395) — zeros + ``last_tick_epoch=None`` instead of raising.
    """
    pool, _ = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    stats = scheduler.get_stats()

    # All seven contract keys present.
    assert set(stats.keys()) == {
        "is_running",
        "registered_job_count",
        "jobs_run",
        "jobs_succeeded",
        "jobs_failed",
        "last_tick_epoch",
        "next_run_epoch",
    }
    # Zero state before any fires.
    assert stats["is_running"] is False
    assert stats["registered_job_count"] == 0
    assert stats["jobs_run"] == 0
    assert stats["jobs_succeeded"] == 0
    assert stats["jobs_failed"] == 0
    assert stats["last_tick_epoch"] is None
    assert stats["next_run_epoch"] is None


def test_get_stats_reflects_in_process_counters():
    """Counters bump as the runner records fires — verified by mutating
    the same private fields the runner mutates."""
    import time as _time

    pool, _ = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    scheduler._jobs_run = 7
    scheduler._jobs_succeeded = 6
    scheduler._jobs_failed = 1
    scheduler._last_tick_epoch = _time.time()

    stats = scheduler.get_stats()
    assert stats["jobs_run"] == 7
    assert stats["jobs_succeeded"] == 6
    assert stats["jobs_failed"] == 1
    assert stats["last_tick_epoch"] is not None
