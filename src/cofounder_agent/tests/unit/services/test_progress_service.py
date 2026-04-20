"""
Unit tests for ProgressService and GenerationProgress.

All tests are pure in-memory — zero DB, LLM, or network calls.
Tests verify progress lifecycle (create → update → complete/fail),
callback registration, cleanup, and the global singleton accessor.
"""

import pytest

from services.progress_service import GenerationProgress, ProgressService, get_progress_service

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> ProgressService:
    """Fresh ProgressService for each test."""
    return ProgressService()


# ---------------------------------------------------------------------------
# GenerationProgress dataclass
# ---------------------------------------------------------------------------


class TestGenerationProgress:
    def test_timestamp_auto_set_on_init(self):
        p = GenerationProgress(task_id="t1", status="pending")
        assert p.timestamp != ""
        assert "T" in p.timestamp  # ISO format contains 'T'

    def test_explicit_timestamp_not_overwritten(self):
        p = GenerationProgress(task_id="t1", status="pending", timestamp="2025-01-01T00:00:00")
        assert p.timestamp == "2025-01-01T00:00:00"

    def test_to_dict_returns_all_fields(self):
        p = GenerationProgress(task_id="abc", status="generating", current_step=3, total_steps=10)
        d = p.to_dict()
        assert d["task_id"] == "abc"
        assert d["status"] == "generating"
        assert d["current_step"] == 3
        assert d["total_steps"] == 10
        assert "timestamp" in d

    def test_defaults(self):
        p = GenerationProgress(task_id="x", status="pending")
        assert p.current_step == 0
        assert p.total_steps == 0
        assert p.percentage == 0.0
        assert p.current_stage == ""
        assert p.elapsed_time == 0.0
        assert p.estimated_remaining == 0.0
        assert p.error is None
        assert p.message == ""


# ---------------------------------------------------------------------------
# ProgressService.create_progress
# ---------------------------------------------------------------------------


class TestCreateProgress:
    def test_creates_with_pending_status(self, service):
        p = service.create_progress("task-1")
        assert p.status == "pending"
        assert p.task_id == "task-1"

    def test_default_total_steps(self, service):
        p = service.create_progress("task-1")
        assert p.total_steps == 50

    def test_custom_total_steps(self, service):
        p = service.create_progress("task-2", total_steps=100)
        assert p.total_steps == 100

    def test_progress_stored_in_service(self, service):
        service.create_progress("task-3")
        assert service.get_progress("task-3") is not None

    def test_message_initialized(self, service):
        p = service.create_progress("task-4")
        assert len(p.message) > 0


# ---------------------------------------------------------------------------
# ProgressService.get_progress
# ---------------------------------------------------------------------------


class TestGetProgress:
    def test_returns_none_for_unknown_task(self, service):
        assert service.get_progress("nonexistent") is None

    def test_returns_progress_after_creation(self, service):
        service.create_progress("task-5")
        p = service.get_progress("task-5")
        assert p is not None
        assert p.task_id == "task-5"


# ---------------------------------------------------------------------------
# ProgressService.update_progress
# ---------------------------------------------------------------------------


class TestUpdateProgress:
    def test_creates_progress_if_missing(self, service):
        # update_progress auto-creates if task not found
        p = service.update_progress("new-task", current_step=1, total_steps=10)
        assert p.task_id == "new-task"
        assert p.status == "generating"

    def test_updates_current_step(self, service):
        service.create_progress("task-u1", total_steps=20)
        p = service.update_progress("task-u1", current_step=5)
        assert p.current_step == 5

    def test_percentage_calculated_correctly(self, service):
        service.create_progress("task-u2", total_steps=10)
        p = service.update_progress("task-u2", current_step=5)
        assert p.percentage == pytest.approx(50.0)

    def test_percentage_at_100_when_complete_step(self, service):
        service.create_progress("task-u3", total_steps=10)
        p = service.update_progress("task-u3", current_step=10)
        assert p.percentage == pytest.approx(100.0)

    def test_stage_updated(self, service):
        service.create_progress("task-u4")
        p = service.update_progress("task-u4", current_step=1, stage="base_model")
        assert p.current_stage == "base_model"

    def test_message_updated(self, service):
        service.create_progress("task-u5")
        p = service.update_progress("task-u5", current_step=1, message="Halfway done")
        assert p.message == "Halfway done"

    def test_elapsed_time_updated(self, service):
        service.create_progress("task-u6", total_steps=10)
        p = service.update_progress("task-u6", current_step=5, elapsed_time=10.0)
        assert p.elapsed_time == pytest.approx(10.0)

    def test_estimated_remaining_calculated(self, service):
        service.create_progress("task-u7", total_steps=10)
        # 5 steps done in 10 seconds → 1 second/step → 5 remaining → 5 seconds left
        p = service.update_progress("task-u7", current_step=5, elapsed_time=5.0)
        assert p.estimated_remaining == pytest.approx(5.0)

    def test_total_steps_overridable(self, service):
        service.create_progress("task-u8", total_steps=10)
        p = service.update_progress("task-u8", current_step=1, total_steps=20)
        assert p.total_steps == 20

    def test_status_set_to_generating(self, service):
        service.create_progress("task-u9")
        p = service.update_progress("task-u9", current_step=1)
        assert p.status == "generating"

    def test_no_division_by_zero_when_total_steps_zero(self, service):
        service.create_progress("task-u10", total_steps=0)
        p = service.update_progress("task-u10", current_step=0)
        # Should not raise; percentage stays 0
        assert p.percentage == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# ProgressService.mark_complete
# ---------------------------------------------------------------------------


class TestMarkComplete:
    def test_status_set_to_completed(self, service):
        service.create_progress("task-c1", total_steps=5)
        p = service.mark_complete("task-c1")
        assert p.status == "completed"

    def test_percentage_100(self, service):
        service.create_progress("task-c2", total_steps=5)
        p = service.mark_complete("task-c2")
        assert p.percentage == pytest.approx(100.0)

    def test_current_step_equals_total_steps(self, service):
        service.create_progress("task-c3", total_steps=8)
        p = service.mark_complete("task-c3")
        assert p.current_step == 8

    def test_estimated_remaining_zero(self, service):
        service.create_progress("task-c4", total_steps=5)
        p = service.mark_complete("task-c4")
        assert p.estimated_remaining == pytest.approx(0.0)

    def test_custom_completion_message(self, service):
        service.create_progress("task-c5")
        p = service.mark_complete("task-c5", message="All done!")
        assert p.message == "All done!"

    def test_default_completion_message(self, service):
        service.create_progress("task-c6")
        p = service.mark_complete("task-c6")
        assert "complete" in p.message.lower()

    def test_returns_none_for_unknown_task(self, service):
        result = service.mark_complete("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# ProgressService.mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    def test_status_set_to_failed(self, service):
        service.create_progress("task-f1")
        p = service.mark_failed("task-f1", error="Connection timeout")
        assert p.status == "failed"

    def test_error_stored(self, service):
        service.create_progress("task-f2")
        p = service.mark_failed("task-f2", error="Out of memory")
        assert p.error == "Out of memory"

    def test_message_contains_error(self, service):
        service.create_progress("task-f3")
        p = service.mark_failed("task-f3", error="Disk full")
        assert "Disk full" in p.message

    def test_returns_none_for_unknown_task(self, service):
        result = service.mark_failed("nonexistent", error="anything")
        assert result is None


# ---------------------------------------------------------------------------
# ProgressService callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callback_called_on_update(self, service):
        received = []
        service.create_progress("task-cb1", total_steps=10)
        service.register_callback("task-cb1", lambda p: received.append(p.current_step))
        service.update_progress("task-cb1", current_step=3)
        assert received == [3]

    def test_callback_called_on_complete(self, service):
        received = []
        service.create_progress("task-cb2", total_steps=5)
        service.register_callback("task-cb2", lambda p: received.append(p.status))
        service.mark_complete("task-cb2")
        assert "completed" in received

    def test_callback_called_on_fail(self, service):
        received = []
        service.create_progress("task-cb3")
        service.register_callback("task-cb3", lambda p: received.append(p.status))
        service.mark_failed("task-cb3", error="boom")
        assert "failed" in received

    def test_multiple_callbacks_all_called(self, service):
        calls = []
        service.create_progress("task-cb4", total_steps=5)
        service.register_callback("task-cb4", lambda p: calls.append("a"))
        service.register_callback("task-cb4", lambda p: calls.append("b"))
        service.update_progress("task-cb4", current_step=1)
        assert "a" in calls
        assert "b" in calls

    def test_failing_callback_does_not_raise(self, service):
        def bad_callback(p):
            raise RuntimeError("callback error")

        service.create_progress("task-cb5")
        service.register_callback("task-cb5", bad_callback)
        # Should not propagate the exception
        service.update_progress("task-cb5", current_step=1)

    def test_no_callbacks_for_other_tasks(self, service):
        received = []
        service.create_progress("task-A", total_steps=5)
        service.create_progress("task-B", total_steps=5)
        service.register_callback("task-A", lambda p: received.append("A"))
        service.update_progress("task-B", current_step=1)
        assert received == []


# ---------------------------------------------------------------------------
# ProgressService.cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_progress(self, service):
        service.create_progress("task-cl1")
        service.cleanup("task-cl1")
        assert service.get_progress("task-cl1") is None

    def test_cleanup_removes_callbacks(self, service):
        received = []
        service.create_progress("task-cl2")
        service.register_callback("task-cl2", lambda p: received.append(1))
        service.cleanup("task-cl2")
        # Recreate and update — old callback should not fire
        service.create_progress("task-cl2")
        service.update_progress("task-cl2", current_step=1)
        assert received == []

    def test_cleanup_unknown_task_does_not_raise(self, service):
        service.cleanup("nonexistent")  # Should be a no-op


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGetProgressService:
    def test_returns_progress_service_instance(self):
        svc = get_progress_service()
        assert isinstance(svc, ProgressService)

    def test_returns_same_instance_on_repeated_calls(self):
        svc1 = get_progress_service()
        svc2 = get_progress_service()
        assert svc1 is svc2
