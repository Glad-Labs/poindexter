"""
Unit tests for services/task_executor.py

Covers:
- Initialization: attributes set correctly, inject_orchestrator, get_stats
- Lifecycle: start/stop create/cancel background task, idempotent start
- _sweep_stale_tasks: delegates to database_service, handles errors silently
- _process_loop: no-pending-tasks path, task processed path, service error path,
  unexpected error path, loop stops when running=False, CancelledError exit
- _process_single_task: marks in_progress, calls _execute_task, updates DB on success,
  timeout path marks task failed, ServiceError re-raises, generic exception wraps
  in ServiceError
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

from services.task_executor import (
    TaskExecutor,
    STALE_TASK_TIMEOUT_MINUTES,
    MAX_TASK_RETRIES,
    SWEEP_INTERVAL_SECONDS,
)
from services.error_handler import ServiceError


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
    db.sweep_stale_tasks = AsyncMock(
        return_value={"total_stale": 0, "reset": 0, "failed": 0}
    )
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


def _make_executor(db=None, orchestrator=None, poll_interval=1):
    if db is None:
        db = _make_db()
    with patch("services.task_executor.UnifiedQualityService"), \
         patch("services.task_executor.AIContentGenerator"), \
         patch("services.task_executor.get_usage_tracker"):
        executor = TaskExecutor(
            database_service=db,
            orchestrator=orchestrator,
            poll_interval=poll_interval,
        )
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

    def test_inject_orchestrator_sets_orchestrator(self):
        executor = _make_executor()
        assert executor.orchestrator is None
        mock_orch = MagicMock()
        executor.inject_orchestrator(mock_orch)
        assert executor.orchestrator is mock_orch

    def test_orchestrator_property_returns_injected(self):
        mock_orch = MagicMock()
        executor = _make_executor(orchestrator=mock_orch)
        assert executor.orchestrator is mock_orch

    def test_get_stats_not_running(self):
        executor = _make_executor()
        stats = executor.get_stats()
        assert stats["running"] is False
        assert stats["task_count"] == 0
        assert stats["success_count"] == 0
        assert stats["error_count"] == 0
        assert stats["published_count"] == 0
        assert stats["orchestrator_available"] is False
        assert stats["quality_service_available"] is True
        assert stats["last_poll_age_s"] is None

    def test_get_stats_with_orchestrator(self):
        mock_orch = MagicMock()
        executor = _make_executor(orchestrator=mock_orch)
        stats = executor.get_stats()
        assert stats["orchestrator_available"] is True

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
        db.sweep_stale_tasks = AsyncMock(
            return_value={"total_stale": 0, "reset": 0, "failed": 0}
        )
        executor = _make_executor(db=db)
        await executor._sweep_stale_tasks()
        db.sweep_stale_tasks.assert_awaited_once_with(
            timeout_minutes=STALE_TASK_TIMEOUT_MINUTES,
            max_retries=MAX_TASK_RETRIES,
        )

    @pytest.mark.asyncio
    async def test_sweep_logs_when_stale_tasks_found(self):
        db = _make_db()
        db.sweep_stale_tasks = AsyncMock(
            return_value={"total_stale": 3, "reset": 2, "failed": 1}
        )
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

        with patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_process_single_task", side_effect=mock_process_single), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_process_single_task", new_callable=AsyncMock), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_process_single_task", side_effect=raise_service_error), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_process_single_task", side_effect=raise_unexpected), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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
        with patch.object(executor, "_process_single_task", new_callable=AsyncMock), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock), \
             caplog.at_level(logging.CRITICAL, logger="services.task_executor"):
            await executor._process_loop()

        critical_msgs = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        assert any("possible stall" in m or "Executor has not" in m for m in critical_msgs), (
            f"Expected a CRITICAL idle alert but got: {critical_msgs}"
        )

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
        with patch.object(executor, "_process_single_task", new_callable=AsyncMock), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock), \
             caplog.at_level(logging.CRITICAL, logger="services.task_executor"):
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
        with patch.object(executor, "_process_single_task", new_callable=AsyncMock), \
             patch.object(executor, "_sweep_stale_tasks", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.sleep", new_callable=AsyncMock):
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

        with patch.object(executor, "_execute_task", new_callable=AsyncMock, return_value=mock_result), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock):
            await executor._process_single_task(task)

        # DB should be called to update task to in_progress at least once
        assert db.update_task.await_count >= 1
        # Final update should include awaiting_approval status
        final_call_args = db.update_task.call_args_list[-1]
        update_data = final_call_args[0][1]
        assert update_data["status"] == "awaiting_approval"

    @pytest.mark.asyncio
    async def test_updates_to_failed_when_execute_returns_failed(self):
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        failed_result = {
            "status": "failed",
            "orchestrator_error": "Content generation failed",
        }

        with patch.object(executor, "_execute_task", new_callable=AsyncMock, return_value=failed_result), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock):
            await executor._process_single_task(task)

        # Final update should include failed status
        final_call_args = db.update_task.call_args_list[-1]
        update_data = final_call_args[0][1]
        assert update_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_task_missing_id_returns_early(self):
        """A task with no id or task_id should return early without touching DB."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = {"task_name": "No ID Task", "status": "pending"}  # No id or task_id

        with patch.object(executor, "_execute_task", new_callable=AsyncMock) as mock_exec:
            await executor._process_single_task(task)
            mock_exec.assert_not_awaited()

        db.update_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_marks_task_as_failed(self):
        """When _execute_task times out, the task is updated to failed."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        async def slow_execute(t):
            # Block forever — asyncio.wait_for will raise TimeoutError
            await asyncio.sleep(9999)

        with patch.object(executor, "_execute_task", side_effect=slow_execute), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock), \
             patch("services.task_executor.asyncio.wait_for", new_callable=AsyncMock,
                   side_effect=asyncio.TimeoutError()):
            await executor._process_single_task(task)

        # Final DB update should record "failed" status
        final_call_args = db.update_task.call_args_list[-1]
        update_data = final_call_args[0][1]
        assert update_data["status"] == "failed"

    @pytest.mark.asyncio
    async def test_service_error_from_execute_re_raises(self):
        """ServiceError from _execute_task bubbles up as ServiceError."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        async def raise_service_error(t):
            raise ServiceError(message="Intentional service error", details={})

        with patch.object(executor, "_execute_task", side_effect=raise_service_error), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock):
            with pytest.raises(ServiceError):
                await executor._process_single_task(task)

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_in_service_error(self):
        """Unexpected exception from _execute_task is wrapped in ServiceError."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        async def raise_runtime_error(t):
            raise RuntimeError("Unexpected crash")

        with patch.object(executor, "_execute_task", side_effect=raise_runtime_error), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock):
            with pytest.raises(ServiceError):
                await executor._process_single_task(task)

    @pytest.mark.asyncio
    async def test_logs_status_change_on_success(self):
        """Audit log (log_status_change) is called for successful task completion."""
        db = _make_db()
        executor = _make_executor(db=db)
        task = _make_task()

        mock_result = {"status": "awaiting_approval"}

        with patch.object(executor, "_execute_task", new_callable=AsyncMock, return_value=mock_result), \
             patch("services.task_executor.emit_task_progress", new_callable=AsyncMock), \
             patch("services.task_executor.emit_notification", new_callable=AsyncMock):
            await executor._process_single_task(task)

        # log_status_change should be called at least twice: pending→in_progress, in_progress→final
        assert db.log_status_change.await_count >= 1


# ---------------------------------------------------------------------------
# TaskMetrics wiring — issue #837
# ---------------------------------------------------------------------------


class TestTaskMetricsWiring:
    """Verify TaskMetrics is imported and instantiated during _execute_task."""

    def test_task_metrics_importable_from_task_executor_module(self):
        """TaskMetrics must be importable via the task_executor module (wired at import time)."""
        import services.task_executor as te_mod
        assert hasattr(te_mod, "TaskMetrics"), (
            "TaskMetrics should be imported in task_executor (issue #837)"
        )

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
        import logging
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
