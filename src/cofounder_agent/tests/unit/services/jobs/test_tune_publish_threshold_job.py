"""Unit tests for ``services/jobs/tune_publish_threshold.py``.

The decision matrix is pure and tested directly (no pool). The UPDATE +
audit_log paths use a mock pool.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.jobs.tune_publish_threshold import (
    TunePublishThresholdJob,
    _compute_adjustment,
)


def _make_pool(
    stats_row: dict | None = None,
    current_threshold: Any = "75",
    fetchrow_raises: BaseException | None = None,
    fetchval_raises: BaseException | None = None,
    execute_raises: BaseException | None = None,
) -> Any:
    conn = AsyncMock()
    if fetchrow_raises is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_raises)
    else:
        conn.fetchrow = AsyncMock(return_value=stats_row)
    if fetchval_raises is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_raises)
    else:
        conn.fetchval = AsyncMock(return_value=current_threshold)
    if execute_raises is not None:
        conn.execute = AsyncMock(side_effect=execute_raises)
    else:
        conn.execute = AsyncMock(return_value="UPDATE 1")

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


# ---------------------------------------------------------------------------
# Pure decision matrix
# ---------------------------------------------------------------------------


class TestComputeAdjustment:
    def test_high_failure_rate_large_decrease(self):
        adj, reason = _compute_adjustment(
            fail_rate=60, pass_rate=30, avg_score=70, current_threshold=75, step=3,
        )
        assert adj == -3
        assert "high failure" in reason

    def test_high_pass_but_low_score_raise(self):
        adj, reason = _compute_adjustment(
            fail_rate=5, pass_rate=92, avg_score=65, current_threshold=75, step=3,
        )
        assert adj == 3
        assert "low avg score" in reason

    def test_moderate_failure_small_decrease(self):
        adj, reason = _compute_adjustment(
            fail_rate=35, pass_rate=60, avg_score=72, current_threshold=75, step=3,
        )
        assert adj == -1
        assert "moderate" in reason

    def test_excellent_quality_small_increase(self):
        adj, reason = _compute_adjustment(
            fail_rate=3, pass_rate=97, avg_score=90, current_threshold=75, step=3,
        )
        assert adj == 1
        assert "excellent" in reason

    def test_quiet_middle_no_change(self):
        adj, reason = _compute_adjustment(
            fail_rate=20, pass_rate=75, avg_score=78, current_threshold=75, step=3,
        )
        assert adj == 0
        assert "no change" in reason

    def test_custom_step_honored(self):
        adj, _ = _compute_adjustment(
            fail_rate=60, pass_rate=30, avg_score=70, current_threshold=75, step=5,
        )
        assert adj == -5


# ---------------------------------------------------------------------------
# Contract + Job.run
# ---------------------------------------------------------------------------


class TestContract:
    def test_has_required_attrs(self):
        job = TunePublishThresholdJob()
        assert job.name == "tune_publish_threshold"
        assert job.schedule == "every 6 hours"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_insufficient_samples_skipped(self):
        pool, conn = _make_pool(
            stats_row={"total": 3, "published": 1, "failed": 0, "rejected": 0, "avg_score": 70},
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {"min_samples": 10})
        assert result.ok is True
        assert result.changes_made == 0
        assert "insufficient" in result.detail
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nudge_applied_when_signals_warrant(self):
        """60% failure, 30 tasks → threshold should drop from 75 to 72."""
        pool, conn = _make_pool(
            stats_row={
                "total": 30, "published": 10, "failed": 18, "rejected": 2, "avg_score": 68,
            },
            current_threshold="75",
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 1
        assert result.metrics["new_threshold"] == 72
        # UPDATE fired twice: once on threshold, once on audit_log.
        assert conn.execute.await_count == 2
        # First execute = UPDATE app_settings, arg[1] is the new value.
        first_call = conn.execute.await_args_list[0]
        assert first_call.args[1] == "72"

    @pytest.mark.asyncio
    async def test_clamps_at_minimum(self):
        """Current threshold already at min — decrease should no-op."""
        pool, conn = _make_pool(
            stats_row={
                "total": 30, "published": 5, "failed": 25, "rejected": 0, "avg_score": 60,
            },
            current_threshold="50",
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {"min_threshold": 50})

        assert result.ok is True
        assert result.changes_made == 0
        assert "boundary" in result.metrics["reason"]
        # No UPDATE — adjustment was clamped out.
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clamps_at_maximum(self):
        pool, conn = _make_pool(
            stats_row={
                "total": 30, "published": 29, "failed": 0, "rejected": 1, "avg_score": 90,
            },
            current_threshold="90",
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {"max_threshold": 90})

        assert result.ok is True
        assert result.changes_made == 0
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_current_threshold_when_absent(self):
        """If the app_settings row is missing, default to 75."""
        pool, conn = _make_pool(
            stats_row={
                "total": 30, "published": 10, "failed": 18, "rejected": 2, "avg_score": 68,
            },
            current_threshold=None,  # fetchval returns None
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {})
        assert result.metrics["current_threshold"] == 75

    @pytest.mark.asyncio
    async def test_custom_min_threshold_respected(self):
        """A custom min_threshold config should clamp to that, not the default 50."""
        pool, _ = _make_pool(
            stats_row={
                "total": 30, "published": 5, "failed": 25, "rejected": 0, "avg_score": 60,
            },
            current_threshold="65",
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {"min_threshold": 65})
        # At the custom min; can't go lower.
        assert result.changes_made == 0
        assert "boundary" in result.metrics["reason"]

    @pytest.mark.asyncio
    async def test_stats_fetch_failure_returns_not_ok(self):
        pool, _ = _make_pool(fetchrow_raises=RuntimeError("pool closed"))
        job = TunePublishThresholdJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "pool closed" in result.detail

    @pytest.mark.asyncio
    async def test_update_failure_returns_not_ok(self):
        """If the threshold UPDATE fails, surface that loudly."""
        pool, _ = _make_pool(
            stats_row={
                "total": 30, "published": 10, "failed": 18, "rejected": 2, "avg_score": 68,
            },
            execute_raises=RuntimeError("row locked"),
        )
        job = TunePublishThresholdJob()
        result = await job.run(pool, {})
        assert result.ok is False
        assert "UPDATE failed" in result.detail

    @pytest.mark.asyncio
    async def test_audit_log_failure_does_not_abort_change(self):
        """If audit_log insert fails AFTER successful threshold change, the
        change still counts. We don't want a stale audit_log table to block
        meaningful threshold tuning."""
        call_count = {"n": 0}

        async def _execute_side_effect(*args: Any) -> str:
            call_count["n"] += 1
            # 1st execute = threshold UPDATE → succeed; 2nd = audit_log → fail.
            if call_count["n"] == 2:
                raise RuntimeError("audit_log missing")
            return "UPDATE 1"

        pool, _ = _make_pool(
            stats_row={
                "total": 30, "published": 10, "failed": 18, "rejected": 2, "avg_score": 68,
            },
        )
        # Override conn.execute manually to get two different side effects.
        pool.acquire.return_value.__aenter__.return_value.execute = AsyncMock(
            side_effect=_execute_side_effect,
        )

        job = TunePublishThresholdJob()
        result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 1
