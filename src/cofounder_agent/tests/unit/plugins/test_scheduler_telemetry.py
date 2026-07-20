"""Unit tests for ``PluginScheduler._record_last_run`` telemetry write.

Lives at the unit layer because the integration harness's session-scope
fixture machinery currently can't run async-fixture-requiring tests
(pre-existing ``ScopeMismatch`` between ``migrations_applied`` and
``_function_scoped_runner``). These tests stub the pool directly so they
need neither real Postgres nor the broken fixture chain.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from plugins.scheduler import PluginScheduler

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests. An explicit mark wrongly tagged the
# sync tests here, emitting a PytestWarning (Glad-Labs/poindexter#997).


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


def _pool_with_fetchval(value):
    """Fake pool whose connection.fetchval returns ``value``."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=value)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))
    return pool


# ---------------------------------------------------------------------------
# _interval_next_run — restart-survival anchoring (2026-05-26 daily-job stall)
# ---------------------------------------------------------------------------


async def test_interval_next_run_none_for_cron_trigger():
    """Cron triggers compute their own next fire — we don't override them."""
    scheduler = PluginScheduler(_pool_with_fetchval(datetime.now(timezone.utc)))
    trigger = CronTrigger.from_crontab("0 13 * * *")
    assert await scheduler._interval_next_run("x", trigger) is None


async def test_interval_next_run_seeds_short_delay_when_no_persisted_run():
    """poindexter#561: a never-run interval job must get an explicit first-fire
    shortly after boot. Returning None let APScheduler re-anchor to
    boot+interval every restart, so a >restart-cadence interval (e.g. 7d
    run_newsletter) never fired."""
    from plugins.scheduler import _FIRST_FIRE_BASE_DELAY_S, _FIRST_FIRE_STAGGER_S

    scheduler = PluginScheduler(_pool_with_fetchval(None))
    trigger = IntervalTrigger(days=7)
    before = datetime.now(timezone.utc)
    nxt = await scheduler._interval_next_run("collapse_old_embeddings", trigger)
    assert nxt is not None
    delay = (nxt - before).total_seconds()
    # Fires within the base delay + stagger window, NOT 7 days out.
    assert _FIRST_FIRE_BASE_DELAY_S - 2 <= delay <= (
        _FIRST_FIRE_BASE_DELAY_S + _FIRST_FIRE_STAGGER_S + 2
    )


async def test_interval_next_run_first_fire_is_deterministic_and_staggered():
    """The first-fire offset is a stable per-name hash (not the salted builtin),
    so it reproduces across calls and de-correlates across jobs."""
    scheduler = PluginScheduler(_pool_with_fetchval(None))
    trigger = IntervalTrigger(days=7)
    a1 = await scheduler._interval_next_run("job_a", trigger)
    a2 = await scheduler._interval_next_run("job_a", trigger)
    b1 = await scheduler._interval_next_run("job_b", trigger)
    assert a1 is not None and a2 is not None and b1 is not None
    # Same job → same offset on repeat (deterministic).
    assert abs((a1 - a2).total_seconds()) < 2
    # Different jobs → different offsets (staggered, not a thundering herd).
    assert abs((a1 - b1).total_seconds()) >= 1


async def test_interval_next_run_fires_now_when_overdue():
    """The 2026-05-26 bug: a 24h job last run ~3.8 days ago must fire on the
    next tick, not get pushed 24h past this boot."""
    last = time.time() - 3.8 * 86400  # ~3.8 days ago
    scheduler = PluginScheduler(
        _pool_with_fetchval(datetime.fromtimestamp(last, tz=timezone.utc))
    )
    trigger = IntervalTrigger(hours=24)
    before = datetime.now(timezone.utc)
    nxt = await scheduler._interval_next_run("x", trigger)
    after = datetime.now(timezone.utc)
    assert nxt is not None
    # Overdue → returns ~now, not last+24h (which would be ~2.8 days ago).
    assert before <= nxt <= after


async def test_interval_next_run_anchors_to_last_run_when_not_due():
    """Not-yet-due job keeps its real cadence: next fire = last_run + interval,
    anchored to the actual last run rather than this boot."""
    last = time.time() - 3600  # 1h ago
    scheduler = PluginScheduler(
        _pool_with_fetchval(datetime.fromtimestamp(last, tz=timezone.utc))
    )
    trigger = IntervalTrigger(hours=24)
    nxt = await scheduler._interval_next_run("x", trigger)
    assert nxt is not None
    expected = datetime.fromtimestamp(last + 86400, tz=timezone.utc)
    assert abs((nxt - expected).total_seconds()) < 2


async def test_seed_job_config_inserts_default_schedule_do_nothing():
    """First registration seeds a DB-tunable config blob with the job's
    default schedule, using ON CONFLICT DO NOTHING so operator edits stick."""
    import json

    pool, conn = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    job = MagicMock()
    job.name = "rollup_post_performance"
    job.schedule = "every 24 hours"

    await scheduler._seed_job_config_if_absent(job)

    assert conn.execute.await_count == 1
    sql, key, value, desc = conn.execute.await_args.args
    assert "ON CONFLICT (key) DO NOTHING" in sql
    assert key == "plugin.job.rollup_post_performance"
    blob = json.loads(value)
    assert blob["enabled"] is True
    assert blob["config"]["schedule"] == "every 24 hours"


async def test_seed_job_config_swallows_db_errors():
    """A seed failure must never block job registration."""
    class _BrokenPool:
        def acquire(self):
            raise RuntimeError("pool down")

    job = MagicMock()
    job.name = "x"
    job.schedule = "every 1 hour"
    # Must not raise.
    await PluginScheduler(_BrokenPool())._seed_job_config_if_absent(job)


async def test_persisted_last_run_epoch_reads_timestamp_and_handles_missing():
    dt = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)
    got = await PluginScheduler(_pool_with_fetchval(dt))._persisted_last_run_epoch("x")
    assert got == dt.timestamp()
    assert await PluginScheduler(_pool_with_fetchval(None))._persisted_last_run_epoch("x") is None


async def test_record_last_run_upserts_ok_row():
    """A successful fire UPSERTs one job_run_state row with status='ok'."""
    pool, conn = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    await scheduler._record_last_run("backfill_podcasts", ok=True)

    assert conn.execute.await_count == 1
    sql, name, status = conn.execute.await_args.args
    assert "job_run_state" in sql
    assert "ON CONFLICT (job_name) DO UPDATE" in sql
    assert name == "backfill_podcasts"
    assert status == "ok"


async def test_record_last_run_upserts_err_row():
    """ok=False writes status='err'."""
    pool, conn = _pool_with_conn()
    scheduler = PluginScheduler(pool)

    await scheduler._record_last_run("backfill_videos", ok=False)

    _, name, status = conn.execute.await_args.args
    assert name == "backfill_videos"
    assert status == "err"


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
