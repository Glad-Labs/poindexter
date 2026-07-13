"""Unit tests for ``PluginScheduler._capture_job_metrics`` — the generic
``JobResult.metrics`` → ``audit_log`` sink (Glad-Labs/poindexter#853).

Every scheduled job returns a ``JobResult`` whose ``metrics`` dict was
previously discarded by the runner. ``_capture_job_metrics`` writes a
``event_type='job_run'`` row into ``audit_log`` for every *metrics-emitting*
fire so a Grafana panel can read it via the Postgres datasource (the same seam
Findings / QA-Rails already use).

Stubs the pool directly (like ``test_scheduler_telemetry.py``) so the tests
need neither real Postgres nor the integration harness's broken async-fixture
chain. ``AuditLogger.log`` calls ``pool.execute(...)`` directly, so the fake
pool exposes ``execute`` as an ``AsyncMock``.
"""

from __future__ import annotations

import json
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock

from plugins.job import JobResult
from plugins.scheduler import PluginScheduler

# No module-level asyncio mark: ``asyncio_mode = "auto"`` (pyproject.toml)
# already auto-marks coroutine tests.


def _pool_with_execute():
    """Fake pool whose ``execute`` is awaitable — AuditLogger.log calls it
    directly (not via ``acquire()``)."""
    pool = MagicMock()
    pool.execute = AsyncMock()
    return pool


def _audit_call_args(pool):
    """Unpack the positional args AuditLogger.log passed to ``pool.execute``:
    ``(INSERT_SQL, event_type, source, task_id, details_json, severity)``.
    """
    args = pool.execute.await_args.args
    sql, event_type, source, task_id, details_json, severity = args
    return sql, event_type, source, task_id, json.loads(details_json), severity


async def test_capture_writes_job_run_row_for_metrics_emitting_fire():
    """A successful fire that emitted metrics writes exactly one
    ``event_type='job_run'`` audit row with the documented ``details`` shape."""
    pool = _pool_with_execute()
    scheduler = PluginScheduler(pool)

    result = JobResult(
        ok=True,
        detail="attempted 3, crossposted 2, 0 errors",
        changes_made=2,
        metrics={"posts_attempted": 3, "posts_crossposted": 2, "errors": 0},
    )

    await scheduler._capture_job_metrics(
        "crosspost_to_devto", result, duration_ms=1234
    )

    assert pool.execute.await_count == 1
    sql, event_type, source, task_id, details, severity = _audit_call_args(pool)
    assert "INSERT INTO audit_log" in sql
    assert event_type == "job_run"
    assert source == "crosspost_to_devto"
    assert task_id is None
    assert severity == "info"  # below the findings_alert_router waterline
    assert details["ok"] is True
    assert details["changes_made"] == 2
    assert details["duration_ms"] == 1234
    # Job-emitted keys are namespaced under ``metrics`` so they can't shadow
    # the envelope fields.
    assert details["metrics"] == {
        "posts_attempted": 3,
        "posts_crossposted": 2,
        "errors": 0,
    }


async def test_capture_records_failed_fire_that_still_emitted_metrics():
    """The gate is on ``metrics``, independent of ``ok`` — an ok=False fire
    that still produced telemetry is recorded (with ok:false captured)."""
    pool = _pool_with_execute()
    scheduler = PluginScheduler(pool)

    result = JobResult(
        ok=False, detail="1 broken link", changes_made=0, metrics={"errors": 1}
    )

    await scheduler._capture_job_metrics(
        "check_published_links", result, duration_ms=50
    )

    assert pool.execute.await_count == 1
    _, _, _, _, details, _ = _audit_call_args(pool)
    assert details["ok"] is False
    assert details["metrics"] == {"errors": 1}


async def test_capture_skips_when_metrics_empty():
    """Metric-less jobs already have job_run_state + failure escalation;
    writing their fires would only bloat audit_log, so they're skipped."""
    pool = _pool_with_execute()
    scheduler = PluginScheduler(pool)

    result = JobResult(ok=True, detail="nothing to do", changes_made=0)  # metrics={}

    await scheduler._capture_job_metrics("some_job", result, duration_ms=5)

    assert pool.execute.await_count == 0


async def test_capture_respects_master_switch_off():
    """``scheduler_job_metrics_capture_enabled=false`` disables the sink."""
    pool = _pool_with_execute()
    cfg = MagicMock()
    cfg.get_bool = MagicMock(return_value=False)
    # Pass tz explicitly so __init__ doesn't resolve tz off the mock site_config.
    scheduler = PluginScheduler(pool, site_config=cfg, tz=timezone.utc)

    result = JobResult(ok=True, detail="x", changes_made=1, metrics={"k": 1})

    await scheduler._capture_job_metrics("some_job", result, duration_ms=5)

    assert pool.execute.await_count == 0
    cfg.get_bool.assert_called_once_with(
        "scheduler_job_metrics_capture_enabled", True
    )


async def test_capture_swallows_db_errors():
    """A broken pool must never crash the scheduler loop — telemetry is
    observability-only."""

    class _BrokenPool:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("pool is dead")

    scheduler = PluginScheduler(_BrokenPool())
    result = JobResult(ok=True, detail="x", changes_made=1, metrics={"k": 1})

    # Must NOT raise.
    await scheduler._capture_job_metrics("some_job", result, duration_ms=5)
