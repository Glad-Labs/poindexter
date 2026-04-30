"""Unit tests for brain/brain_daemon.py auto_remediate — GH-90.

The stale-task sweeper lives in ``brain/brain_daemon.py``. GH-90 requires:

1. Sweeper UPDATE is guarded by ``updated_at < NOW() - interval`` (not
   just ``started_at``) so a fresh heartbeat from the worker prevents
   auto-cancel.
2. Every auto-cancel emits a warn-level log with the task_id + reason.
3. Every auto-cancel inserts a ``task.auto_cancelled`` row in
   ``pipeline_events`` so the Prometheus exporter can surface the count.

All DB I/O is mocked via AsyncMock.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the poindexter distro.
# brain_daemon.py uses bare ``from health_probes import ...`` for
# runtime-container compatibility, so we add brain/ directly to sys.path
# BEFORE importing. Tests without this prelude fail to collect with
# ``ModuleNotFoundError: No module named 'health_probes'``.
#
# Path: tests/unit/services/test_brain_daemon_auto_remediate.py
# parents[0] = services/
# parents[1] = unit/
# parents[2] = tests/
# parents[3] = cofounder_agent/
# parents[4] = src/
# parents[5] = repo root (contains brain/)
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


def _make_pool_for_sweeper(
    stale_minutes: str = "180",
    grace_minutes: str = "10",
    stuck_rows: list[dict] | None = None,
):
    """Build a mock pool that returns the settings + stuck-task rows in
    the order ``auto_remediate`` queries them.

    Query order in auto_remediate (only the parts the GH-90 test cares
    about):

    1. SELECT value FROM app_settings WHERE key = 'stale_task_timeout_minutes'
    2. SELECT value FROM app_settings WHERE key = 'brain_auto_cancel_grace_minutes'
    3. UPDATE pipeline_tasks ... RETURNING task_id, topic   (stuck_rows)
    4. SELECT value FROM app_settings WHERE key = 'brain_stale_approval_auto_reject_days'
    5. UPDATE pipeline_tasks ... awaiting_approval expire   (empty)
    6. SELECT ... pending/active/last_task                  (fetchrow)
    7. SELECT value FROM app_settings WHERE key = 'brain_failure_rate_window_hours'
    8. SELECT recent_fails, recent_total                    (fetchrow)

    Only the sweeper path is exercised — the downstream branches are
    fed empty/safe values.
    """
    pool = MagicMock()
    stuck_rows = stuck_rows if stuck_rows is not None else []

    # fetchval is used by _setting_int to read the stale_minutes value.
    # Order of _setting_int calls matches the sweeper code:
    # stale_task_timeout_minutes → brain_auto_cancel_grace_minutes →
    # brain_stale_approval_auto_reject_days → brain_failure_rate_window_hours.
    pool.fetchval = AsyncMock(side_effect=[
        stale_minutes, grace_minutes,
        "7",   # approval auto-reject days
        "24",  # failure rate window hours
    ])

    # fetch is used by the sweeper UPDATE (returns stuck rows) and the
    # awaiting_approval expire UPDATE (returns []).
    pool.fetch = AsyncMock(side_effect=[stuck_rows, []])

    # fetchrow: pipeline stall query, then failure rate query.
    pool.fetchrow = AsyncMock(side_effect=[
        {"pending": 0, "active": 1, "last_task": None},
        {"recent_fails": 0, "recent_total": 0},
    ])

    # execute: swallows any emit (e.g. pipeline_events insert).
    pool.execute = AsyncMock()

    return pool


@pytest.mark.unit
@pytest.mark.asyncio
class TestAutoRemediateSweeper:
    """GH-90: the sweeper must guard on updated_at freshness + log + emit event."""

    async def test_sweeper_uses_updated_at_and_grace_in_where_clause(self):
        """The SQL sent by the sweeper must include BOTH
        ``updated_at < NOW() - INTERVAL`` AND
        ``COALESCE(started_at, updated_at) < NOW() - INTERVAL``. A fresh
        heartbeat updates ``updated_at``, and that alone must be enough
        to make the sweeper skip the row (the old behaviour relied
        solely on ``started_at`` which is set-once-on-start and never
        moved)."""
        pool = _make_pool_for_sweeper(
            stale_minutes="180", grace_minutes="10",
            stuck_rows=[],
        )
        await bd.auto_remediate(pool)

        # The sweeper UPDATE is the first fetch call.
        assert pool.fetch.call_count >= 1
        sweeper_sql = pool.fetch.call_args_list[0].args[0]

        # GH-90 core guards.
        assert "UPDATE pipeline_tasks" in sweeper_sql
        assert "status = 'in_progress'" in sweeper_sql
        assert "updated_at < NOW() - INTERVAL" in sweeper_sql
        # The cutoff uses stale + grace minutes (190 total).
        assert "190 minutes" in sweeper_sql
        # Legacy started_at guard is kept as a belt-and-suspenders
        # check (protects rows that legitimately never ran).
        assert "COALESCE(started_at, updated_at)" in sweeper_sql

    async def test_sweeper_emits_pipeline_event_per_cancel(self):
        """GH-90 AC #4: each auto-cancelled task gets a
        ``task.auto_cancelled`` row in pipeline_events. The Prometheus
        exporter reads the count on scrape, so this is the persistent
        signal operators see in the dashboard."""
        stuck = [
            {"task_id": "bbb07318", "topic": "The Shadow Price of Speed"},
            {"task_id": "ccc12345", "topic": "Another stuck task topic"},
        ]
        pool = _make_pool_for_sweeper(stuck_rows=stuck)
        await bd.auto_remediate(pool)

        # execute() is called once per cancelled task to insert the event.
        # (It may also be called elsewhere in auto_remediate for the
        # idle-alert branch, but only when hours_idle > 48 with
        # last_task set — our mock sets last_task=None so that branch
        # is skipped.)
        exec_sqls = [c.args[0] for c in pool.execute.call_args_list]
        auto_cancel_inserts = [
            s for s in exec_sqls
            if "INSERT INTO pipeline_events" in s
            and "task.auto_cancelled" in s
        ]
        assert len(auto_cancel_inserts) == len(stuck), (
            f"expected {len(stuck)} pipeline_events inserts, "
            f"got {len(auto_cancel_inserts)}: {exec_sqls}"
        )

    async def test_sweeper_logs_warning_per_cancelled_task(self, caplog):
        """GH-90 AC #4: one warn log per cancelled task containing its
        task_id + reason. Operators grep by task_id to correlate with
        worker logs."""
        import logging

        stuck = [
            {"task_id": "bbb07318", "topic": "The Shadow Price of Speed"},
        ]
        pool = _make_pool_for_sweeper(stuck_rows=stuck)

        with caplog.at_level(logging.WARNING, logger="brain"):
            await bd.auto_remediate(pool)

        warn_messages = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING
        ]
        # Find the specific auto-cancel warn line.
        auto_cancel_lines = [
            m for m in warn_messages
            if "[auto-cancel]" in m and "bbb07318" in m
        ]
        assert auto_cancel_lines, (
            "expected a warn-level log containing task_id and '[auto-cancel]'; "
            f"got: {warn_messages}"
        )
        line = auto_cancel_lines[0]
        assert "bbb07318" in line
        assert "stuck in_progress" in line
        assert "180m" in line  # stale_minutes (not cutoff_minutes) in the message

    async def test_sweeper_does_nothing_when_no_stuck_rows(self):
        """Cleanroom check: no stuck rows → no events, no warn logs."""
        pool = _make_pool_for_sweeper(stuck_rows=[])
        await bd.auto_remediate(pool)

        exec_sqls = [c.args[0] for c in pool.execute.call_args_list]
        auto_cancel_inserts = [
            s for s in exec_sqls
            if "task.auto_cancelled" in s
        ]
        assert auto_cancel_inserts == []

    async def test_bump_helper_inserts_one_event_per_count(self):
        """_bump_auto_cancelled_metric inserts exactly N rows for a
        caller's count=N."""
        pool = MagicMock()
        pool.execute = AsyncMock()

        await bd._bump_auto_cancelled_metric(pool, 3)

        assert pool.execute.await_count == 3
        for call in pool.execute.call_args_list:
            assert "task.auto_cancelled" in call.args[0]

    async def test_bump_helper_noop_when_count_zero(self):
        """Defensive: count<=0 is a no-op, no DB roundtrips."""
        pool = MagicMock()
        pool.execute = AsyncMock()
        await bd._bump_auto_cancelled_metric(pool, 0)
        assert pool.execute.await_count == 0
