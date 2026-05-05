"""Unit tests for brain/brain_daemon.py auto_remediate — GH-90.

The stale-task sweeper lives in ``brain/brain_daemon.py``. GH-90 requires:

1. Sweeper UPDATE is guarded by ``updated_at < NOW() - interval`` (not
   just ``started_at``) so a fresh heartbeat from the worker prevents
   auto-cancel.
2. Every auto-cancel emits a warn-level log with the task_id + reason.
3. Every auto-cancel inserts a ``task.auto_cancelled`` row in
   ``pipeline_tasks.auto_cancelled_at`` so the Prometheus exporter can surface the count.

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

    # execute: swallows any emit (e.g. pipeline_tasks UPDATE for the
    # auto_cancelled_at stamp written by _stamp_auto_cancelled).
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

    async def test_sweeper_stamps_auto_cancelled_at_per_cancel(self):
        """GH-90 AC #4: each auto-cancelled task gets its
        ``pipeline_tasks.auto_cancelled_at`` column stamped (Phase 2 of
        poindexter#366 moved this off pipeline_events). The Prometheus
        exporter reads ``COUNT(*) WHERE auto_cancelled_at IS NOT NULL``
        on scrape, so this is the persistent signal operators see in
        the dashboard.
        """
        stuck = [
            {"task_id": "bbb07318", "topic": "The Shadow Price of Speed"},
            {"task_id": "ccc12345", "topic": "Another stuck task topic"},
        ]
        pool = _make_pool_for_sweeper(stuck_rows=stuck)
        await bd.auto_remediate(pool)

        exec_sqls = [c.args[0] for c in pool.execute.call_args_list]
        # _stamp_auto_cancelled fires ONE UPDATE for all stuck task_ids
        # via ANY($1::text[]), not one INSERT per row like the old
        # pipeline_events flow.
        stamps = [
            s for s in exec_sqls
            if "UPDATE pipeline_tasks" in s
            and "auto_cancelled_at" in s
            and "ANY($1::text[])" in s
        ]
        assert len(stamps) == 1, (
            f"expected 1 _stamp_auto_cancelled UPDATE, got "
            f"{len(stamps)}: {exec_sqls}"
        )
        # And the task_ids array passed in matches the stuck list.
        stamp_call = next(
            c for c in pool.execute.call_args_list
            if "auto_cancelled_at" in c.args[0]
        )
        assert stamp_call.args[1] == [r["task_id"] for r in stuck]

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
        """Cleanroom check: no stuck rows → no auto_cancelled stamps."""
        pool = _make_pool_for_sweeper(stuck_rows=[])
        await bd.auto_remediate(pool)

        exec_sqls = [c.args[0] for c in pool.execute.call_args_list]
        stamps = [s for s in exec_sqls if "auto_cancelled_at" in s]
        assert stamps == []

    async def test_stamp_helper_one_update_for_n_tasks(self):
        """``_stamp_auto_cancelled`` issues a single batched UPDATE for
        a caller's task_ids list, not one statement per row."""
        pool = MagicMock()
        pool.execute = AsyncMock()

        await bd._stamp_auto_cancelled(pool, ["t-1", "t-2", "t-3"])

        assert pool.execute.await_count == 1
        sql = pool.execute.call_args.args[0]
        assert "UPDATE pipeline_tasks" in sql
        assert "auto_cancelled_at" in sql
        assert "ANY($1::text[])" in sql
        # Second positional arg is the task_ids array.
        assert pool.execute.call_args.args[1] == ["t-1", "t-2", "t-3"]

    async def test_stamp_helper_noop_when_empty_list(self):
        """Defensive: empty task_ids is a no-op, no DB roundtrips."""
        pool = MagicMock()
        pool.execute = AsyncMock()
        await bd._stamp_auto_cancelled(pool, [])
        assert pool.execute.await_count == 0
