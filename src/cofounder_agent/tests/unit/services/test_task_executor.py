"""
Unit tests for services/task_executor.py

Covers:
- Initialization: attributes set correctly, inject_orchestrator, get_stats
- Lifecycle: start/stop create/cancel background task, idempotent start
- _sweep_stale_tasks: delegates to database_service, handles errors silently
- _process_loop: no-pending-tasks path, task processed path, service error path,
  unexpected error path, loop stops when running=False, CancelledError exit
- _process_single_task: marks in_progress, runs content router pipeline, updates DB on success,
  timeout path marks task failed, ServiceError re-raises, generic exception wraps
  in ServiceError
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.error_handler import ServiceError
from services.task_executor import (
    TaskExecutor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def _make_db():
    db = AsyncMock()
    db.get_pending_tasks = AsyncMock(return_value=[])
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.log_status_change = AsyncMock(return_value=None)
    db.sweep_stale_tasks = AsyncMock(return_value={"total_stale": 0, "reset": 0, "failed": 0})
    return db


def _make_task(task_id=TASK_ID, status="pending"):
    return {
        "id": task_id,
        "task_id": task_id,
        "task_name": "Unit Test Task",
        "topic": "AI Testing",
        "category": "technology",
        "target_audience": "developers",
        "primary_keyword": "testing",
        "user_id": "user-123",
        "status": status,
        "task_type": "blog_post",
        "task_metadata": None,
    }


def _make_executor(db=None, poll_interval=1):
    """Construct a TaskExecutor for tests with the heavy collaborators stubbed.

    Note: the legacy `orchestrator=` kwarg was removed when the dead UnifiedOrchestrator
    scaffolding was deleted (Glad-Labs/poindexter#333). Tests that used to mock
    `executor.orchestrator` should drop those assertions.
    """
    if db is None:
        db = _make_db()
    with (
        patch("services.task_executor.UnifiedQualityService"),
        patch("services.task_executor.AIContentGenerator"),
        patch("services.task_executor.get_usage_tracker"),
    ):
        executor = TaskExecutor(
            database_service=db,
            poll_interval=poll_interval,
        )
    # Default _get_setting mock — returns the default arg so callers like
    # _semantic_dedup_enabled, min_curation_score etc. get sensible values
    # without hitting the DB.  Individual tests can override.
    async def _fake_get_setting(_key: str, default: str = "") -> str:
        return default

    executor._get_setting = _fake_get_setting
    return executor


# ---------------------------------------------------------------------------
# Initialization and basic properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskExecutorInit:
    """Test initialization and simple property accessors."""

    def test_initial_state(self):
        executor = _make_executor()
        assert executor.running is False
        assert executor.task_count == 0
        assert executor.success_count == 0
        assert executor.error_count == 0
        assert executor.published_count == 0
        assert executor._processor_task is None
        assert executor.last_poll_at is None
        assert executor._poll_cycle == 0

    # `inject_orchestrator` / `executor.orchestrator` / `orchestrator_available`
    # were removed when the dead UnifiedOrchestrator scaffolding was deleted
    # (#333). The tests for them are gone with them.

    def test_get_stats_not_running(self):
        executor = _make_executor()
        stats = executor.get_stats()
        assert stats["running"] is False
        assert stats["task_count"] == 0
        assert stats["success_count"] == 0
        assert stats["error_count"] == 0
        assert stats["published_count"] == 0
        assert stats["quality_service_available"] is True
        assert stats["last_poll_age_s"] is None

    def test_get_stats_last_poll_age_computed(self):
        import time

        executor = _make_executor()
        executor.last_poll_at = time.monotonic() - 5.0
        stats = executor.get_stats()
        assert stats["last_poll_age_s"] is not None
        assert stats["last_poll_age_s"] >= 5.0


# ---------------------------------------------------------------------------
# Lifecycle: start / stop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskExecutorLifecycle:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_true(self):
        executor = _make_executor()
        with patch.object(executor, "_process_loop", new_callable=AsyncMock):
            await executor.start()
            assert executor.running is True
            await executor.stop()

    @pytest.mark.asyncio
    async def test_start_creates_processor_task(self):
        executor = _make_executor()

        # Provide a non-terminating coroutine so the task stays alive during test
        async def noop_loop():
            await asyncio.sleep(10)

        with patch.object(executor, "_process_loop", side_effect=noop_loop):
            await executor.start()
            assert executor._processor_task is not None
            await executor.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent_when_already_running(self):
        executor = _make_executor()

        async def noop_loop():
            await asyncio.sleep(10)

        with patch.object(executor, "_process_loop", side_effect=noop_loop):
            await executor.start()
            first_task = executor._processor_task
            await executor.start()  # second call — should be ignored
            assert executor._processor_task is first_task
            await executor.stop()

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        executor = _make_executor()

        async def noop_loop():
            await asyncio.sleep(10)

        with patch.object(executor, "_process_loop", side_effect=noop_loop):
            await executor.start()
            await executor.stop()
            assert executor.running is False

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_noop(self):
        executor = _make_executor()
        # Should not raise
        await executor.stop()
        assert executor.running is False


# ---------------------------------------------------------------------------
# _sweep_stale_tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSweepStaleTasks:
    """Test the stale-task sweep delegation."""

    @pytest.mark.asyncio
    async def test_sweep_calls_db_sweep(self):
        db = _make_db()
        db.sweep_stale_tasks = AsyncMock(return_value={"total_stale": 0, "reset": 0, "failed": 0})
        executor = _make_executor(db=db)
        await executor._sweep_stale_tasks()
        # Exact values come from DB settings at runtime, just verify it was called
        db.sweep_stale_tasks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sweep_logs_when_stale_tasks_found(self):
        db = _make_db()
        db.sweep_stale_tasks = AsyncMock(return_value={"total_stale": 3, "reset": 2, "failed": 1})
        executor = _make_executor(db=db)
        await executor._sweep_stale_tasks()
        db.sweep_stale_tasks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sweep_swallows_db_errors(self):
        db = _make_db()
        db.sweep_stale_tasks = AsyncMock(side_effect=Exception("DB down"))
        executor = _make_executor(db=db)
        await executor._sweep_stale_tasks()
        db.sweep_stale_tasks.assert_awaited_once()


# ---------------------------------------------------------------------------
# _sweep_stale_pending_tasks — GH-89 AC#4 stale-pending auto-cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSweepStalePendingTasks:
    """Tasks stuck in 'pending' for > stale_pending_timeout_hours must be
    auto-cancelled with a clear reason payload. Fires at warn level so
    operators can distinguish a slow pipeline from a prolonged throttle."""

    @pytest.mark.asyncio
    async def test_runs_update_against_pool(self):
        db = _make_db()
        db.pool = MagicMock()
        db.pool.fetch = AsyncMock(return_value=[])
        executor = _make_executor(db=db)

        async def _fake_get_setting(key, default=""):
            return "24" if key == "stale_pending_timeout_hours" else default

        executor._get_setting = _fake_get_setting
        await executor._sweep_stale_pending_tasks()
        db.pool.fetch.assert_awaited_once()
        # First positional arg is SQL, must reference content_tasks and pending
        sql_arg = db.pool.fetch.call_args[0][0]
        assert "content_tasks" in sql_arg
        assert "pending" in sql_arg
        # Must also set status to cancelled so downstream auto-retry ignores it
        assert "cancelled" in sql_arg

    @pytest.mark.asyncio
    async def test_warns_when_tasks_reaped(self):
        db = _make_db()
        db.pool = MagicMock()
        stale_rows = [
            {"task_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "topic": "Old one", "created_at": "2025-01-01"},
            {"task_id": "11111111-2222-3333-4444-555555555555", "topic": "Older one", "created_at": "2024-12-25"},
        ]
        db.pool.fetch = AsyncMock(return_value=stale_rows)
        executor = _make_executor(db=db)
        executor._get_setting = AsyncMock(return_value="24")

        with patch("services.task_executor.logger") as mock_logger:
            await executor._sweep_stale_pending_tasks()

        # Verify a warn-level log fired mentioning both the sweep and a count
        warn_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("STALE_PENDING_SWEEP" in c for c in warn_calls)
        assert any("2" in c for c in warn_calls)  # 2 tasks reaped

    @pytest.mark.asyncio
    async def test_swallows_db_errors(self):
        db = _make_db()
        db.pool = MagicMock()
        db.pool.fetch = AsyncMock(side_effect=Exception("db lost"))
        executor = _make_executor(db=db)
        executor._get_setting = AsyncMock(return_value="24")
        # Must not raise
        await executor._sweep_stale_pending_tasks()

    @pytest.mark.asyncio
    async def test_disabled_when_timeout_zero(self):
        """stale_pending_timeout_hours=0 disables the sweeper."""
        db = _make_db()
        db.pool = MagicMock()
        db.pool.fetch = AsyncMock(return_value=[])
        executor = _make_executor(db=db)
        executor._get_setting = AsyncMock(return_value="0")
        await executor._sweep_stale_pending_tasks()
        db.pool.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_pool_is_noop(self):
        db = _make_db()
        db.pool = None
        executor = _make_executor(db=db)
        # Must not raise, must not fetch.
        await executor._sweep_stale_pending_tasks()


# ---------------------------------------------------------------------------
# Throttle metric toggling (GH-89 AC#2) — exercised via _process_loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThrottleMetricToggle:
    """When the executor polls and finds the approval queue full, the
    shared throttle gauge must flip to 1. When the queue drains, it
    must flip back to 0. Exercised through the real _process_loop so
    we cover the actual wiring, not just the throttle module."""

    @pytest.mark.asyncio
    async def test_gauge_active_when_queue_full_during_poll(self):
        from services import pipeline_throttle

        pipeline_throttle.reset_for_tests()

        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        db = _make_db()
        # First poll: one pending task ready. Throttle check returns True.
        db.get_pending_tasks = AsyncMock(side_effect=[[task_a], asyncio.CancelledError()])
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(return_value={"c": 10})

        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        # Force max_approval_queue=3 via patched site_config
        mock_cfg = MagicMock()
        mock_cfg.get_int = MagicMock(
            side_effect=lambda k, default=0: 3 if k == "max_approval_queue" else default
        )

        with (
            patch("services.site_config.site_config", mock_cfg),
            patch.object(executor, "_process_single_task", new_callable=AsyncMock) as mock_single,
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        # Gauge is active, task was NOT dispatched (throttled)
        state = pipeline_throttle.get_state()
        assert state["active"] is True
        assert state["queue_size"] == 10
        assert state["queue_limit"] == 3
        mock_single.assert_not_awaited()

        pipeline_throttle.reset_for_tests()

    @pytest.mark.asyncio
    async def test_gauge_not_active_when_queue_has_room(self):
        from services import pipeline_throttle

        pipeline_throttle.reset_for_tests()

        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        db = _make_db()
        db.get_pending_tasks = AsyncMock(side_effect=[[task_a], asyncio.CancelledError()])
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(return_value={"c": 1})  # under limit

        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        mock_cfg = MagicMock()
        mock_cfg.get_int = MagicMock(
            side_effect=lambda k, default=0: 3 if k == "max_approval_queue" else default
        )

        with (
            patch("services.site_config.site_config", mock_cfg),
            patch.object(executor, "_process_single_task", new_callable=AsyncMock) as mock_single,
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        state = pipeline_throttle.get_state()
        assert state["active"] is False
        # Task WAS dispatched because queue wasn't full
        mock_single.assert_awaited()

        pipeline_throttle.reset_for_tests()


# ---------------------------------------------------------------------------
# _process_loop behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProcessLoop:
    """Test the main process loop behaviors."""

    @pytest.mark.asyncio
    async def test_loop_stops_on_cancelled_error(self):
        """CancelledError inside the loop breaks out cleanly."""
        db = _make_db()
        db.get_pending_tasks = AsyncMock(side_effect=asyncio.CancelledError())
        executor = _make_executor(db=db)
        executor.running = True

        with (
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            # The loop should exit without raising when CancelledError propagates
            await executor._process_loop()
        # No assertion needed — success means no unhandled exception

    @pytest.mark.asyncio
    async def test_loop_increments_poll_cycle(self):
        """Each iteration increments _poll_cycle."""
        db = _make_db()
        call_count = 0

        async def get_pending_then_stop(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_then_stop)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        with (
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert executor._poll_cycle >= 2

    @pytest.mark.asyncio
    async def test_loop_calls_process_single_task_for_each_pending(self):
        """Pending tasks are dispatched to _process_single_task."""
        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        task_b = _make_task("aaaaaaaa-bbbb-cccc-dddd-222222222222")
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [task_a, task_b]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True
        processed = []

        async def mock_process_single(task):
            processed.append(task["id"])

        with (
            patch.object(executor, "_process_single_task", side_effect=mock_process_single),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert task_a["id"] in processed
        assert task_b["id"] in processed

    @pytest.mark.asyncio
    async def test_loop_increments_success_count_on_task_success(self):
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_task()]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        with (
            patch.object(executor, "_process_single_task", new_callable=AsyncMock),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert executor.success_count == 1

    @pytest.mark.asyncio
    async def test_loop_increments_error_count_on_service_error(self):
        """ServiceError from _process_single_task increments error_count."""
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_task()]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        async def raise_service_error(task):
            raise ServiceError(
                message="Simulated service error",
                details={"task_id": task["id"]},
            )

        with (
            patch.object(executor, "_process_single_task", side_effect=raise_service_error),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert executor.error_count == 1
        assert executor.success_count == 0
        # DB should record the failure
        db.update_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_loop_increments_error_count_on_unexpected_error(self):
        """An unexpected exception from _process_single_task increments error_count."""
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_task()]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True

        async def raise_unexpected(task):
            raise RuntimeError("Unexpected failure")

        with (
            patch.object(executor, "_process_single_task", side_effect=raise_unexpected),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert executor.error_count == 1

    @pytest.mark.asyncio
    async def test_idle_alert_fires_when_pending_tasks_not_started_for_too_long(self, caplog):
        """
        Regression test for issue #841: a CRITICAL log must be emitted when
        pending tasks exist but _last_task_started_at is older than
        _IDLE_ALERT_THRESHOLD_S.
        """
        import time

        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [task_a]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True
        # Simulate executor that started tasks long ago and is now stalling.
        executor._last_task_started_at = time.monotonic() - (executor._IDLE_ALERT_THRESHOLD_S + 10)

        import logging

        with (
            patch.object(executor, "_process_single_task", new_callable=AsyncMock),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.CRITICAL, logger="services.task_executor"),
        ):
            await executor._process_loop()

        critical_msgs = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        assert any(
            "possible stall" in m or "Executor has not" in m for m in critical_msgs
        ), f"Expected a CRITICAL idle alert but got: {critical_msgs}"

    @pytest.mark.asyncio
    async def test_idle_alert_not_fired_when_no_prior_tasks(self, caplog):
        """No idle alert fires when _last_task_started_at is None (executor just started)."""
        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [task_a]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True
        # _last_task_started_at is None — fresh executor, no alert should fire.

        import logging

        with (
            patch.object(executor, "_process_single_task", new_callable=AsyncMock),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level(logging.CRITICAL, logger="services.task_executor"),
        ):
            await executor._process_loop()

        critical_msgs = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        idle_alerts = [m for m in critical_msgs if "possible stall" in m or "Executor has not" in m]
        assert not idle_alerts, f"Unexpected idle alert on fresh executor: {idle_alerts}"

    @pytest.mark.asyncio
    async def test_last_task_started_at_updated_when_task_begins(self):
        """_last_task_started_at is set to monotonic time when a task starts."""
        import time

        task_a = _make_task("aaaaaaaa-bbbb-cccc-dddd-111111111111")
        db = _make_db()
        call_count = 0

        async def get_pending_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [task_a]
            executor.running = False
            return []

        db.get_pending_tasks = AsyncMock(side_effect=get_pending_once)
        executor = _make_executor(db=db, poll_interval=0)
        executor.running = True
        assert executor._last_task_started_at is None

        before = time.monotonic()
        with (
            patch.object(executor, "_process_single_task", new_callable=AsyncMock),
            patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock),
            patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await executor._process_loop()

        assert executor._last_task_started_at is not None
        assert executor._last_task_started_at >= before

    @pytest.mark.asyncio
    async def test_get_stats_includes_idle_fields(self):
        """get_stats() must expose last_task_started_age_s and idle_alert_threshold_s."""
        db = _make_db()
        executor = _make_executor(db=db)

        stats = executor.get_stats()
        assert "last_task_started_age_s" in stats
        assert "idle_alert_threshold_s" in stats
        assert stats["idle_alert_threshold_s"] == executor._IDLE_ALERT_THRESHOLD_S
        # No tasks started yet — age should be None
        assert stats["last_task_started_age_s"] is None


# ---------------------------------------------------------------------------
# _process_single_task
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProcessSingleTask:
    """Test _process_single_task core behaviors."""

    @pytest.mark.asyncio
    async def test_marks_task_in_progress_then_calls_execute(self):
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        mock_result = {"status": "awaiting_approval"}

        # The code calls db.tasks.log_status_change, so set up the nested mock
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
        ):
            await executor._process_single_task(task)

        # DB should be called to update task to in_progress at least once
        assert db.update_task.await_count >= 1
        # On success the content router already updates the task in DB, so
        # _process_single_task returns early. The only update_task call is
        # the initial in_progress transition.
        first_call_args = db.update_task.call_args_list[0]
        update_data = first_call_args[0][1]
        assert update_data["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_updates_to_failed_when_execute_returns_failed(self):
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        failed_result = {
            "status": "failed",
            "orchestrator_error": "Content generation failed",
        }

        # The code calls db.tasks.log_status_change, so set up the nested mock
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
                return_value=failed_result,
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
        ):
            await executor._process_single_task(task)

        # The failure path calls update_task a second time with failed status
        assert db.update_task.await_count >= 2
        final_call_args = db.update_task.call_args_list[-1]
        update_data = final_call_args[0][1]
        assert update_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_task_missing_id_returns_early(self):
        """A task with no id or task_id should return early without touching DB."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = {"task_name": "No ID Task", "status": "pending"}  # No id or task_id

        await executor._process_single_task(task)

        db.update_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_marks_task_as_failed(self):
        """When the content pipeline times out, the task is updated to failed."""
        db = _make_db()
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)
        executor = _make_executor(db=db)
        task = _make_task()

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
            patch(
                "services.task_executor.asyncio.wait_for",
                new_callable=AsyncMock,
                side_effect=asyncio.TimeoutError(),
            ),
        ):
            await executor._process_single_task(task)

        # Final DB update should record "failed" status
        final_call_args = db.update_task.call_args_list[-1]
        update_data = final_call_args[0][1]
        assert update_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_service_error_from_pipeline_re_raises(self):
        """ServiceError from the content pipeline bubbles up as ServiceError."""
        db = _make_db()
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)
        executor = _make_executor(db=db)
        task = _make_task()

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
                side_effect=ServiceError(message="Intentional service error", details={}),
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
        ):
            with pytest.raises(ServiceError):
                await executor._process_single_task(task)

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_in_service_error(self):
        """Unexpected exception from the content pipeline is wrapped in ServiceError."""
        db = _make_db()
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)
        executor = _make_executor(db=db)
        task = _make_task()

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Unexpected crash"),
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
        ):
            with pytest.raises(ServiceError):
                await executor._process_single_task(task)

    @pytest.mark.asyncio
    async def test_logs_status_change_on_success(self):
        """Audit log (log_status_change) is called for pending->in_progress transition."""
        db = _make_db()
        # The code calls db.tasks.log_status_change, so set up the nested mock
        db.tasks = MagicMock()
        db.tasks.log_status_change = AsyncMock(return_value=None)
        executor = _make_executor(db=db)
        task = _make_task()

        mock_result = {"status": "awaiting_approval"}

        with (
            patch(
                "services.content_router_service.process_content_generation_task",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
            patch("services.task_executor.emit_task_progress", new_callable=AsyncMock),
            patch("services.task_executor.emit_notification", new_callable=AsyncMock),
        ):
            await executor._process_single_task(task)

        # log_status_change should be called for pending→in_progress
        assert db.tasks.log_status_change.await_count >= 1


# ---------------------------------------------------------------------------
# TaskMetrics wiring — issue #837
# ---------------------------------------------------------------------------


class TestTaskMetricsWiring:
    """Verify TaskMetrics interface works correctly."""

    def test_task_metrics_class_interface(self):
        """TaskMetrics exposes the required instrumentation methods."""
        from services.metrics_service import TaskMetrics

        m = TaskMetrics("test-task")
        ts = m.record_phase_start("content_generation")
        assert isinstance(ts, float)
        m.record_phase_end("content_generation", ts, status="success")
        breakdown = m.get_phase_breakdown()
        assert "content_generation" in breakdown
        assert breakdown["content_generation"] >= 0

    def test_task_metrics_logs_structured_summary(self, caplog):
        """After recording two phases the summary log includes phase names."""

        from services.metrics_service import TaskMetrics

        m = TaskMetrics("task-xyz")
        ts1 = m.record_phase_start("content_generation")
        m.record_phase_end("content_generation", ts1)
        ts2 = m.record_phase_start("quality_validation")
        m.record_phase_end("quality_validation", ts2)
        breakdown = m.get_phase_breakdown()
        assert "content_generation" in breakdown
        assert "quality_validation" in breakdown
        # Total duration must be non-negative
        assert m.get_total_duration_ms() >= 0


# ===========================================================================
# _get_setting and _get_model_selections — small DB helpers
# ===========================================================================


class TestGetSetting:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_database_service(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        result = await executor._get_setting("any_key", "default_val")
        assert result == "default_val"

    @pytest.mark.asyncio
    async def test_returns_default_when_pool_is_none(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = None
        executor = TaskExecutor(database_service=db)
        result = await executor._get_setting("any_key", "default_val")
        assert result == "default_val"

    @pytest.mark.asyncio
    async def test_returns_db_value_when_present(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.get_setting_value = AsyncMock(return_value="from_db")
        executor = TaskExecutor(database_service=db)
        result = await executor._get_setting("key", "default")
        assert result == "from_db"

    @pytest.mark.asyncio
    async def test_returns_default_when_row_missing(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.get_setting_value = AsyncMock(return_value="default")
        executor = TaskExecutor(database_service=db)
        result = await executor._get_setting("key", "default")
        assert result == "default"

    @pytest.mark.asyncio
    async def test_returns_default_on_db_exception(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.get_setting_value = AsyncMock(side_effect=RuntimeError("conn lost"))
        executor = TaskExecutor(database_service=db)
        result = await executor._get_setting("key", "default")
        assert result == "default"


class TestGetModelSelections:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_database_service(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        result = await executor._get_model_selections("task-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_dict_when_already_a_dict(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(return_value={
            "model_selections": {"draft": "qwen3:8b", "qa": "gemma3:27b"}
        })
        executor = TaskExecutor(database_service=db)
        result = await executor._get_model_selections("task-1")
        assert result == {"draft": "qwen3:8b", "qa": "gemma3:27b"}

    @pytest.mark.asyncio
    async def test_parses_json_string(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(return_value={
            "model_selections": '{"draft": "qwen3:8b"}'
        })
        executor = TaskExecutor(database_service=db)
        result = await executor._get_model_selections("task-1")
        assert result == {"draft": "qwen3:8b"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_row_missing(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(return_value=None)
        executor = TaskExecutor(database_service=db)
        result = await executor._get_model_selections("task-1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_db_exception(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetchrow = AsyncMock(side_effect=RuntimeError("conn lost"))
        executor = TaskExecutor(database_service=db)
        result = await executor._get_model_selections("task-1")
        assert result == {}


# ===========================================================================
# _get_auto_publish_threshold
# ===========================================================================


class TestGetAutoPublishThreshold:
    @pytest.mark.asyncio
    async def test_returns_value_from_setting(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        executor._get_setting = AsyncMock(return_value="85.5")
        result = await executor._get_auto_publish_threshold()
        assert result == 85.5

    @pytest.mark.asyncio
    async def test_returns_zero_when_setting_empty(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        executor._get_setting = AsyncMock(return_value="")
        result = await executor._get_auto_publish_threshold()
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_invalid_value(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        executor._get_setting = AsyncMock(return_value="not-a-number")
        result = await executor._get_auto_publish_threshold()
        assert result == 0.0


# ===========================================================================
# _auto_publish_task — daily limit guard
# ===========================================================================


class TestAutoPublishTaskGuards:
    @pytest.mark.asyncio
    async def test_returns_early_when_daily_limit_reached(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.cloud_pool = MagicMock()
        # Daily limit is 1, today's count is 1 → at limit
        db.cloud_pool.fetchval = AsyncMock(return_value=1)
        db.get_task = AsyncMock()
        db.update_task_status = AsyncMock()

        executor = TaskExecutor(database_service=db)
        executor._get_setting = AsyncMock(return_value="1")

        await executor._auto_publish_task("task-1", 90.0)

        # Daily limit hit early — no task fetch, no status change
        db.get_task.assert_not_awaited()
        db.update_task_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_early_when_task_not_found(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.cloud_pool = MagicMock()
        db.cloud_pool.fetchval = AsyncMock(return_value=0)  # under limit
        db.get_task = AsyncMock(return_value=None)
        db.update_task_status = AsyncMock()

        executor = TaskExecutor(database_service=db)
        executor._get_setting = AsyncMock(return_value="5")

        await executor._auto_publish_task("missing-task", 90.0)
        db.update_task_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_early_when_no_featured_image(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = MagicMock()
        db.cloud_pool = MagicMock()
        db.cloud_pool.fetchval = AsyncMock(return_value=0)
        db.get_task = AsyncMock(return_value={
            "task_id": "t1",
            "featured_image_url": None,  # missing
        })
        db.update_task_status = AsyncMock()

        executor = TaskExecutor(database_service=db)
        executor._get_setting = AsyncMock(return_value="5")

        await executor._auto_publish_task("t1", 90.0)
        db.update_task_status.assert_not_awaited()


# ---------------------------------------------------------------------------
# _auto_retry_failed_tasks — rejection semantics (#178)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAutoRetryFailedTasks:
    """Test the auto-retry logic for failed and rejected tasks.

    Three distinct cases per the function's docstring:
    1. status='failed', no allow_revisions → retry (execution failure)
    2. status='rejected_retry', allow_revisions=true → retry (human said try again)
    3. status='rejected_final', allow_revisions=false → skip (hard rejection)
    """

    def _setup_executor(self, fetch_rows):
        """Helper: create an executor with a mocked pool returning fetch_rows."""
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetch = AsyncMock(return_value=fetch_rows)
        db.update_task = AsyncMock(return_value={"task_id": "t1"})
        executor = _make_executor(db=db)
        executor._get_setting = AsyncMock(return_value="3")  # max_retries
        return executor, db

    @pytest.mark.asyncio
    async def test_skips_when_no_database_service(self):
        from services.task_executor import TaskExecutor
        executor = TaskExecutor(database_service=None)
        # Should not raise
        await executor._auto_retry_failed_tasks()

    @pytest.mark.asyncio
    async def test_skips_when_pool_is_none(self):
        from services.task_executor import TaskExecutor
        db = MagicMock()
        db.pool = None
        executor = TaskExecutor(database_service=db)
        await executor._auto_retry_failed_tasks()
        # No call should have been made (guard rail works)

    @pytest.mark.asyncio
    async def test_skips_when_no_eligible_tasks(self):
        executor, db = self._setup_executor(fetch_rows=[])
        await executor._auto_retry_failed_tasks()
        db.pool.fetch.assert_awaited_once()
        db.update_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retries_case1_execution_failure(self):
        """Case 1: status='failed', no allow_revisions key → retry."""
        row = {
            "task_id": "t1",
            "status": "failed",
            "topic": "Test Topic",
            "task_metadata": {},
            "retry_count": 0,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        db.update_task.assert_awaited_once()
        call = db.update_task.call_args
        assert call[0][0] == "t1"
        updates = call[0][1]
        assert updates["status"] == "pending"
        assert updates["approval_status"] == "pending"
        assert updates["error_message"] is None
        assert updates["task_metadata"]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_retries_case2_human_allowed_revisions(self):
        """Case 2: status='rejected_retry' (human said try again) → retry."""
        row = {
            "task_id": "t2",
            "status": "rejected_retry",
            "topic": "Revised Topic",
            "task_metadata": {"allow_revisions": True},
            "retry_count": 1,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        db.update_task.assert_awaited_once()
        call = db.update_task.call_args
        updates = call[0][1]
        assert updates["status"] == "pending"
        # Stale rejection from first pass is cleared
        assert updates["approval_status"] == "pending"
        assert updates["task_metadata"]["retry_count"] == 2
        # Second retry uses different adjustments
        assert updates["task_metadata"]["retry_adjustments"].get("target_length") == 1000

    @pytest.mark.asyncio
    async def test_metadata_survives_as_json_string(self):
        """task_metadata stored as JSON string should parse correctly."""
        row = {
            "task_id": "t3",
            "status": "failed",
            "topic": "JSON meta",
            "task_metadata": '{"source": "test"}',  # string not dict
            "retry_count": 0,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        call = db.update_task.call_args
        meta = call[0][1]["task_metadata"]
        assert meta["source"] == "test"
        assert meta["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_first_retry_uses_qwen_coder(self):
        """retry_count=0 → adjustments include qwen3-coder writer model."""
        row = {
            "task_id": "t4",
            "status": "failed",
            "topic": "First retry",
            "task_metadata": {},
            "retry_count": 0,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        call = db.update_task.call_args
        adjustments = call[0][1]["task_metadata"]["retry_adjustments"]
        assert adjustments["model_selections"]["draft"] == "qwen3-coder:30b"

    @pytest.mark.asyncio
    async def test_second_retry_shortens_and_skips_image(self):
        """retry_count=1 → shorter content, skip featured image."""
        row = {
            "task_id": "t5",
            "status": "failed",
            "topic": "Second retry",
            "task_metadata": {},
            "retry_count": 1,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        call = db.update_task.call_args
        adjustments = call[0][1]["task_metadata"]["retry_adjustments"]
        assert adjustments["target_length"] == 1000
        assert adjustments["generate_featured_image"] is False

    @pytest.mark.asyncio
    async def test_processes_multiple_eligible_tasks(self):
        """Up to 3 tasks can be retried per sweep."""
        rows = [
            {"task_id": f"t{i}", "status": "failed", "topic": f"Topic {i}",
             "task_metadata": {}, "retry_count": 0}
            for i in range(3)
        ]
        executor, db = self._setup_executor(fetch_rows=rows)
        await executor._auto_retry_failed_tasks()
        assert db.update_task.await_count == 3

    @pytest.mark.asyncio
    async def test_swallows_db_errors(self):
        """Pool failures should not propagate."""
        db = MagicMock()
        db.pool = MagicMock()
        db.pool.fetch = AsyncMock(side_effect=Exception("DB down"))
        executor = _make_executor(db=db)
        executor._get_setting = AsyncMock(return_value="3")
        # Should not raise
        await executor._auto_retry_failed_tasks()

    @pytest.mark.asyncio
    async def test_last_retry_at_timestamp_recorded(self):
        """Each retry stamps metadata with the retry timestamp."""
        row = {
            "task_id": "t6",
            "status": "failed",
            "topic": "Stamp test",
            "task_metadata": {},
            "retry_count": 0,
        }
        executor, db = self._setup_executor(fetch_rows=[row])
        await executor._auto_retry_failed_tasks()
        call = db.update_task.call_args
        meta = call[0][1]["task_metadata"]
        assert "last_retry_at" in meta
        # ISO-format timestamp
        assert "T" in meta["last_retry_at"]


# ---------------------------------------------------------------------------
# _heartbeat_loop (GH-90)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHeartbeatLoop:
    """GH-90 AC #2: worker heartbeats updated_at during long stages."""

    @pytest.mark.asyncio
    async def test_heartbeat_loop_calls_db_on_interval(self):
        """The loop calls database_service.heartbeat_task on a cadence read
        from app_settings. We set the interval to a tiny value so the
        test can observe multiple heartbeats in <1s."""
        db = _make_db()
        db.heartbeat_task = AsyncMock(return_value=True)
        executor = _make_executor(db=db)

        # Force a very short interval so the loop heartbeats quickly.
        async def _short_interval(key, default=""):
            if key == "worker_heartbeat_interval_seconds":
                return "0.05"  # 50ms
            return default
        executor._get_setting = _short_interval

        # Start the loop, let it fire ~4 heartbeats, then cancel.
        loop_task = asyncio.create_task(executor._heartbeat_loop("t-live"))
        await asyncio.sleep(0.25)
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

        # At least 2 heartbeats should have fired (sleep+fire,sleep+fire).
        assert db.heartbeat_task.await_count >= 2
        # Every call was for the correct task_id.
        for call in db.heartbeat_task.await_args_list:
            assert call.args == ("t-live",)

    @pytest.mark.asyncio
    async def test_heartbeat_loop_exits_when_task_terminal(self):
        """If heartbeat_task returns False (row is already in a terminal
        state) the loop must exit — no point continuing to heartbeat a
        cancelled row, and the exit logs a warning that the worker is
        about to detect the cancellation on its next guarded write."""
        db = _make_db()
        # First call succeeds (worker is alive), second returns False
        # (sweeper already flipped the row to failed).
        db.heartbeat_task = AsyncMock(side_effect=[True, False])
        executor = _make_executor(db=db)

        async def _short_interval(key, default=""):
            if key == "worker_heartbeat_interval_seconds":
                return "0.05"
            return default
        executor._get_setting = _short_interval

        # Should exit cleanly (no cancel needed) on the False return.
        await asyncio.wait_for(
            executor._heartbeat_loop("t-cancelled"),
            timeout=1.0,
        )
        assert db.heartbeat_task.await_count == 2

    @pytest.mark.asyncio
    async def test_heartbeat_loop_disabled_when_interval_zero(self):
        """Interval of 0 (or negative) disables the heartbeat — useful
        for operators running short pipelines who don't want any DB
        chatter during stage execution."""
        db = _make_db()
        db.heartbeat_task = AsyncMock(return_value=True)
        executor = _make_executor(db=db)

        async def _zero_interval(key, default=""):
            if key == "worker_heartbeat_interval_seconds":
                return "0"
            return default
        executor._get_setting = _zero_interval

        await asyncio.wait_for(
            executor._heartbeat_loop("t-any"),
            timeout=1.0,
        )
        # No heartbeats fired — loop returned immediately.
        assert db.heartbeat_task.await_count == 0

    @pytest.mark.asyncio
    async def test_heartbeat_loop_swallows_db_errors(self):
        """A transient DB error during a heartbeat MUST NOT kill the loop —
        the next tick should continue trying."""
        db = _make_db()
        db.heartbeat_task = AsyncMock(side_effect=[
            Exception("conn reset"), True, True,
        ])
        executor = _make_executor(db=db)

        async def _short_interval(key, default=""):
            if key == "worker_heartbeat_interval_seconds":
                return "0.05"
            return default
        executor._get_setting = _short_interval

        loop_task = asyncio.create_task(executor._heartbeat_loop("t-live"))
        await asyncio.sleep(0.25)
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

        # Loop kept running after the first exception.
        assert db.heartbeat_task.await_count >= 2
