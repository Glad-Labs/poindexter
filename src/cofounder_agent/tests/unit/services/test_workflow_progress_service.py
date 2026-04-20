"""
Unit tests for services.workflow_progress_service

Tests cover:
- WorkflowProgress dataclass (timestamp auto-set, to_dict)
- WorkflowProgressService lifecycle (create → start → phases → complete/fail)
- Callback registration, notification, and unregistration
- update_elapsed_time with estimated_remaining calculation
- cleanup removes progress and callbacks
- Global singleton get_workflow_progress_service
- Error in callback is swallowed (no re-raise)
"""

import pytest

from services.workflow_progress_service import (
    WorkflowProgress,
    WorkflowProgressService,
    get_workflow_progress_service,
)

# ---------------------------------------------------------------------------
# WorkflowProgress dataclass
# ---------------------------------------------------------------------------


class TestWorkflowProgress:
    def test_timestamp_auto_set(self):
        p = WorkflowProgress(execution_id="ex-1")
        assert p.timestamp != ""
        assert "T" in p.timestamp  # ISO format

    def test_explicit_timestamp_preserved(self):
        p = WorkflowProgress(execution_id="ex-1", timestamp="2025-01-01T00:00:00")
        assert p.timestamp == "2025-01-01T00:00:00"

    def test_to_dict_includes_all_fields(self):
        p = WorkflowProgress(execution_id="ex-1", workflow_id="wf-1", total_phases=5)
        d = p.to_dict()
        assert d["execution_id"] == "ex-1"
        assert d["workflow_id"] == "wf-1"
        assert d["total_phases"] == 5
        assert "status" in d
        assert "progress_percent" in d

    def test_defaults(self):
        p = WorkflowProgress(execution_id="ex-1")
        assert p.status == "pending"
        assert p.current_phase == 0
        assert p.progress_percent == 0.0
        assert p.completed_phases == 0
        assert p.error is None


# ---------------------------------------------------------------------------
# WorkflowProgressService — create / get
# ---------------------------------------------------------------------------


@pytest.fixture
def svc() -> WorkflowProgressService:
    return WorkflowProgressService()


class TestCreateProgress:
    def test_create_returns_workflow_progress(self, svc):
        p = svc.create_progress("ex-1", workflow_id="wf-1", total_phases=3)
        assert isinstance(p, WorkflowProgress)
        assert p.execution_id == "ex-1"
        assert p.total_phases == 3

    def test_get_progress_after_create(self, svc):
        svc.create_progress("ex-2")
        p = svc.get_progress("ex-2")
        assert p is not None
        assert p.execution_id == "ex-2"

    def test_get_progress_unknown_returns_none(self, svc):
        assert svc.get_progress("nonexistent") is None


# ---------------------------------------------------------------------------
# start_execution
# ---------------------------------------------------------------------------


class TestStartExecution:
    def test_sets_status_to_executing(self, svc):
        svc.create_progress("ex-1")
        p = svc.start_execution("ex-1")
        assert p.status == "executing"

    def test_creates_if_not_exists(self, svc):
        p = svc.start_execution("new-ex")
        assert p is not None
        assert p.status == "executing"

    def test_custom_message_applied(self, svc):
        svc.create_progress("ex-1")
        p = svc.start_execution("ex-1", message="Let's go")
        assert p.message == "Let's go"


# ---------------------------------------------------------------------------
# start_phase / complete_phase / fail_phase
# ---------------------------------------------------------------------------


class TestPhaseLifecycle:
    def test_start_phase_sets_fields(self, svc):
        svc.create_progress("ex-1", total_phases=3)
        p = svc.start_phase("ex-1", 0, "research")
        assert p.current_phase == 0
        assert p.phase_name == "research"
        assert p.phase_status == "executing"

    def test_start_phase_unknown_execution_returns_none(self, svc):
        result = svc.start_phase("unknown", 0, "research")
        assert result is None

    def test_complete_phase_increments_count(self, svc):
        svc.create_progress("ex-1", total_phases=2)
        svc.complete_phase("ex-1", "research")
        p = svc.get_progress("ex-1")
        assert p.completed_phases == 1

    def test_complete_phase_calculates_percent(self, svc):
        svc.create_progress("ex-1", total_phases=4)
        svc.complete_phase("ex-1", "phase1")
        p = svc.get_progress("ex-1")
        assert p.progress_percent == 25.0

    def test_complete_phase_stores_output(self, svc):
        svc.create_progress("ex-1", total_phases=1)
        svc.complete_phase("ex-1", "research", phase_output={"data": "xyz"}, duration_ms=500.0)
        p = svc.get_progress("ex-1")
        assert "research" in p.phase_results
        assert p.phase_results["research"]["output"] == {"data": "xyz"}
        assert p.phase_results["research"]["duration_ms"] == 500.0

    def test_fail_phase_sets_failed_status(self, svc):
        svc.create_progress("ex-1")
        p = svc.fail_phase("ex-1", "creative", "LLM timeout")
        assert p.phase_status == "failed"
        assert "creative" in p.phase_results
        assert p.phase_results["creative"]["error"] == "LLM timeout"

    def test_fail_phase_unknown_execution_returns_none(self, svc):
        result = svc.fail_phase("unknown", "stage", "error")
        assert result is None


# ---------------------------------------------------------------------------
# mark_complete / mark_failed
# ---------------------------------------------------------------------------


class TestMarkCompleteAndFailed:
    def test_mark_complete_sets_status(self, svc):
        svc.create_progress("ex-1")
        p = svc.mark_complete("ex-1", duration_ms=1500.0)
        assert p.status == "completed"
        assert p.progress_percent == 100.0
        assert p.estimated_remaining == 0.0
        assert p.elapsed_time == 1500.0

    def test_mark_complete_stores_final_output(self, svc):
        svc.create_progress("ex-1")
        svc.mark_complete("ex-1", final_output={"blog": "content"})
        p = svc.get_progress("ex-1")
        assert p.phase_results["final_output"] == {"blog": "content"}

    def test_mark_complete_unknown_returns_none(self, svc):
        assert svc.mark_complete("unknown") is None

    def test_mark_failed_sets_status(self, svc):
        svc.create_progress("ex-1")
        p = svc.mark_failed("ex-1", error="db down", failed_phase="research")
        assert p.status == "failed"
        assert p.error == "db down"
        assert "research" in p.message

    def test_mark_failed_no_phase_message(self, svc):
        svc.create_progress("ex-1")
        p = svc.mark_failed("ex-1", error="network error")
        assert "network error" in p.message
        assert p.status == "failed"

    def test_mark_failed_unknown_returns_none(self, svc):
        assert svc.mark_failed("unknown", "error") is None


# ---------------------------------------------------------------------------
# update_elapsed_time
# ---------------------------------------------------------------------------


class TestUpdateElapsedTime:
    def test_sets_elapsed_time(self, svc):
        svc.create_progress("ex-1")
        p = svc.update_elapsed_time("ex-1", 3000.0)
        assert p.elapsed_time == 3000.0

    def test_estimates_remaining_when_partial_progress(self, svc):
        svc.create_progress("ex-1", total_phases=4)
        # Manually set progress_percent to 50
        prog = svc.get_progress("ex-1")
        prog.progress_percent = 50.0
        p = svc.update_elapsed_time("ex-1", 10000.0)
        # time_per_percent = 200ms, remaining_percent = 50 → estimated = 10000ms
        assert p.estimated_remaining == pytest.approx(10000.0, rel=0.01)

    def test_no_estimate_at_zero_progress(self, svc):
        svc.create_progress("ex-1")
        p = svc.update_elapsed_time("ex-1", 5000.0)
        assert p.estimated_remaining == 0.0

    def test_unknown_execution_returns_none(self, svc):
        assert svc.update_elapsed_time("unknown", 100.0) is None


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callback_invoked_on_start(self, svc):
        results = []
        svc.create_progress("ex-1")
        svc.register_callback("ex-1", lambda p: results.append(p.status))
        svc.start_execution("ex-1")
        assert "executing" in results

    def test_callback_invoked_on_complete_phase(self, svc):
        results = []
        svc.create_progress("ex-1", total_phases=2)
        svc.register_callback("ex-1", lambda p: results.append(p.completed_phases))
        svc.complete_phase("ex-1", "phase1")
        assert 1 in results

    def test_unregister_callback_stops_calls(self, svc):
        results = []

        def cb(p):
            results.append(p.status)

        svc.create_progress("ex-1")
        svc.register_callback("ex-1", cb)
        svc.unregister_callback("ex-1", cb)
        svc.start_execution("ex-1")
        assert results == []

    def test_unregister_nonexistent_callback_no_error(self, svc):
        svc.create_progress("ex-1")
        svc.unregister_callback("ex-1", lambda p: None)  # Should not raise

    def test_callback_exception_does_not_propagate(self, svc):
        def bad_cb(p):
            raise RuntimeError("callback failure")

        svc.create_progress("ex-1")
        svc.register_callback("ex-1", bad_cb)
        # Should not raise
        svc.start_execution("ex-1")


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_cleanup_removes_progress(self, svc):
        svc.create_progress("ex-1")
        svc.cleanup("ex-1")
        assert svc.get_progress("ex-1") is None

    def test_cleanup_unknown_no_error(self, svc):
        svc.cleanup("nonexistent")  # Should not raise

    def test_cleanup_removes_callbacks(self, svc):
        results = []
        svc.create_progress("ex-1")
        svc.register_callback("ex-1", lambda p: results.append(1))
        svc.cleanup("ex-1")
        # Recreate and start — old callback should be gone
        svc.create_progress("ex-1")
        svc.start_execution("ex-1")
        assert results == []


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def test_returns_workflow_progress_service(self):
        svc = get_workflow_progress_service()
        assert isinstance(svc, WorkflowProgressService)

    def test_same_instance_returned_twice(self):
        a = get_workflow_progress_service()
        b = get_workflow_progress_service()
        assert a is b
