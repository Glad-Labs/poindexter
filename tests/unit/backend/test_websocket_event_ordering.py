"""
WebSocket event ordering tests for the WorkflowProgressService.

Verifies that:
- Events are emitted in the correct lifecycle order
- Phase transitions produce the expected sequence of status snapshots
- Callbacks fire synchronously and in order
- Failed phases and failed executions emit the right sequence
- Multiple independent executions do not cross-contaminate callbacks
- Progress percentage only increases (monotonic)
"""

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "src" / "cofounder_agent"))

# ── stub structlog before any import touches it ──────────────────────────────
if "structlog" not in sys.modules:
    _stub = ModuleType("structlog")
    setattr(_stub, "get_logger", lambda *a, **k: MagicMock())
    sys.modules["structlog"] = _stub


from services.workflow_progress_service import WorkflowProgress, WorkflowProgressService


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_service() -> WorkflowProgressService:
    return WorkflowProgressService()


def _capture_events(service: WorkflowProgressService, execution_id: str) -> list:
    """Register a callback that records every emitted progress snapshot."""
    events: list[WorkflowProgress] = []

    def _cb(progress: WorkflowProgress) -> None:
        # Store a shallow copy so mutations don't overwrite history
        import copy
        events.append(copy.copy(progress))

    service.register_callback(execution_id, _cb)
    return events


# ── lifecycle ordering ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_happy_path_event_order_single_phase():
    """create → start → start_phase → complete_phase → mark_complete gives expected statuses."""
    svc = _make_service()
    events = []

    svc.create_progress("exec-1", total_phases=1)
    events.append(("post-create", svc.get_progress("exec-1").status))

    svc.register_callback("exec-1", lambda p: events.append(("cb", p.status, p.phase_status)))

    svc.start_execution("exec-1")
    svc.start_phase("exec-1", 0, "Research")
    svc.complete_phase("exec-1", "Research", phase_output={"data": "ok"})
    svc.mark_complete("exec-1", message="Done")

    statuses = [e[1] for e in events if e[0] == "cb"]
    assert statuses == ["executing", "executing", "executing", "completed"]


@pytest.mark.unit
def test_happy_path_three_phases_all_complete():
    """Three phases produce three start + three complete + one final-complete event."""
    svc = _make_service()
    svc.create_progress("exec-2", total_phases=3)
    events = _capture_events(svc, "exec-2")

    svc.start_execution("exec-2")
    for i, name in enumerate(["Research", "Draft", "Publish"]):
        svc.start_phase("exec-2", i, name)
        svc.complete_phase("exec-2", name)
    svc.mark_complete("exec-2")

    assert len(events) == 8  # start_execution + (start_phase + complete_phase)*3 + mark_complete
    assert events[0].status == "executing"
    assert events[-1].status == "completed"
    assert events[-1].progress_percent == 100.0


@pytest.mark.unit
def test_phase_statuses_alternate_executing_completed():
    """Phase status alternates executing → completed with each phase."""
    svc = _make_service()
    svc.create_progress("exec-3", total_phases=2)
    events = _capture_events(svc, "exec-3")

    svc.start_execution("exec-3")
    svc.start_phase("exec-3", 0, "A")
    svc.complete_phase("exec-3", "A")
    svc.start_phase("exec-3", 1, "B")
    svc.complete_phase("exec-3", "B")
    svc.mark_complete("exec-3")

    phase_statuses = [e.phase_status for e in events[1:]]  # skip start_execution
    assert phase_statuses == [
        "executing",  # start_phase A
        "completed",  # complete_phase A
        "executing",  # start_phase B
        "completed",  # complete_phase B
        "completed",  # mark_complete
    ]


@pytest.mark.unit
def test_progress_percent_is_monotonically_increasing():
    """Progress percentage must never decrease across the event sequence."""
    svc = _make_service()
    svc.create_progress("exec-4", total_phases=4)
    events = _capture_events(svc, "exec-4")

    svc.start_execution("exec-4")
    for i, name in enumerate(["A", "B", "C", "D"]):
        svc.start_phase("exec-4", i, name)
        svc.complete_phase("exec-4", name)
    svc.mark_complete("exec-4")

    percents = [e.progress_percent for e in events]
    for a, b in zip(percents, percents[1:]):
        assert b >= a, f"Progress went backwards: {a} → {b}"

    assert percents[-1] == 100.0


# ── failure scenarios ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_failed_phase_emits_failed_phase_status():
    """fail_phase sets phase_status='failed' and emits the event."""
    svc = _make_service()
    svc.create_progress("exec-fail-1", total_phases=2)
    events = _capture_events(svc, "exec-fail-1")

    svc.start_execution("exec-fail-1")
    svc.start_phase("exec-fail-1", 0, "Research")
    svc.fail_phase("exec-fail-1", "Research", error="API timeout")

    last = events[-1]
    assert last.phase_status == "failed"
    assert "API timeout" in last.message


@pytest.mark.unit
def test_mark_failed_sets_execution_status_failed():
    """mark_failed sets status='failed' and includes error text."""
    svc = _make_service()
    svc.create_progress("exec-fail-2", total_phases=1)
    events = _capture_events(svc, "exec-fail-2")

    svc.start_execution("exec-fail-2")
    svc.start_phase("exec-fail-2", 0, "Research")
    svc.mark_failed("exec-fail-2", error="DB connection lost", failed_phase="Research")

    last = events[-1]
    assert last.status == "failed"
    assert "DB connection lost" in last.message
    assert "Research" in last.message


@pytest.mark.unit
def test_failed_then_complete_not_possible():
    """mark_complete after mark_failed should still emit, status tracks last call."""
    svc = _make_service()
    svc.create_progress("exec-fail-3", total_phases=1)
    events = _capture_events(svc, "exec-fail-3")

    svc.start_execution("exec-fail-3")
    svc.mark_failed("exec-fail-3", error="boom")
    svc.mark_complete("exec-fail-3")  # caller mistake — service still emits

    # Last event should reflect mark_complete
    assert events[-1].status == "completed"
    assert len(events) == 3  # start + fail + complete


# ── event isolation across executions ────────────────────────────────────────


@pytest.mark.unit
def test_callbacks_isolated_per_execution():
    """A callback registered for exec-A does NOT fire for exec-B events."""
    svc = _make_service()
    svc.create_progress("exec-A", total_phases=1)
    svc.create_progress("exec-B", total_phases=1)

    events_a: list = []
    svc.register_callback("exec-A", lambda p: events_a.append(p.execution_id))

    svc.start_execution("exec-A")
    svc.start_execution("exec-B")  # should NOT trigger exec-A callback
    svc.mark_complete("exec-A")
    svc.mark_complete("exec-B")  # should NOT trigger exec-A callback

    assert all(eid == "exec-A" for eid in events_a)
    assert len(events_a) == 2  # start_execution + mark_complete for exec-A only


@pytest.mark.unit
def test_multiple_callbacks_all_fire():
    """Multiple callbacks registered for the same execution all receive events."""
    svc = _make_service()
    svc.create_progress("exec-multi", total_phases=1)

    received_1: list = []
    received_2: list = []
    received_3: list = []

    svc.register_callback("exec-multi", lambda p: received_1.append(p.status))
    svc.register_callback("exec-multi", lambda p: received_2.append(p.status))
    svc.register_callback("exec-multi", lambda p: received_3.append(p.status))

    svc.start_execution("exec-multi")
    svc.mark_complete("exec-multi")

    assert received_1 == ["executing", "completed"]
    assert received_2 == received_1
    assert received_3 == received_1


# ── phase result storage ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_phase_results_stored_after_complete_phase():
    """complete_phase stores output in phase_results keyed by phase name."""
    svc = _make_service()
    svc.create_progress("exec-res", total_phases=1)
    svc.start_execution("exec-res")
    svc.start_phase("exec-res", 0, "Research")
    svc.complete_phase("exec-res", "Research", phase_output={"articles": 5}, duration_ms=300.0)

    p = svc.get_progress("exec-res")
    assert "Research" in p.phase_results
    assert p.phase_results["Research"]["status"] == "completed"
    assert p.phase_results["Research"]["output"] == {"articles": 5}
    assert p.phase_results["Research"]["duration_ms"] == 300.0


@pytest.mark.unit
def test_multiple_phase_results_accumulated():
    """Each completed phase's output is stored independently."""
    svc = _make_service()
    svc.create_progress("exec-multi-res", total_phases=3)
    svc.start_execution("exec-multi-res")

    for i, (name, out) in enumerate([
        ("Research", {"sources": 3}),
        ("Draft", {"words": 500}),
        ("QA", {"score": 9.5}),
    ]):
        svc.start_phase("exec-multi-res", i, name)
        svc.complete_phase("exec-multi-res", name, phase_output=out)

    p = svc.get_progress("exec-multi-res")
    assert len(p.phase_results) == 3
    assert p.phase_results["Research"]["output"] == {"sources": 3}
    assert p.phase_results["QA"]["output"] == {"score": 9.5}


# ── get_progress / unknown execution ─────────────────────────────────────────


@pytest.mark.unit
def test_get_progress_unknown_execution_returns_none():
    """get_progress for a non-existent execution_id returns None."""
    svc = _make_service()
    assert svc.get_progress("does-not-exist") is None


@pytest.mark.unit
def test_operations_on_unknown_execution_return_none():
    """Service methods gracefully handle unknown execution IDs."""
    svc = _make_service()
    assert svc.start_phase("ghost", 0, "x") is None
    assert svc.complete_phase("ghost", "x") is None
    assert svc.fail_phase("ghost", "x", "err") is None
    assert svc.mark_complete("ghost") is None
    assert svc.mark_failed("ghost", "err") is None


# ── completed_phases counter ──────────────────────────────────────────────────


@pytest.mark.unit
def test_completed_phases_counter_increments_correctly():
    """completed_phases increments by 1 for each complete_phase call."""
    svc = _make_service()
    svc.create_progress("exec-counter", total_phases=3)
    svc.start_execution("exec-counter")

    for i, name in enumerate(["A", "B", "C"]):
        svc.start_phase("exec-counter", i, name)
        svc.complete_phase("exec-counter", name)
        p = svc.get_progress("exec-counter")
        assert p.completed_phases == i + 1


@pytest.mark.unit
def test_current_phase_index_advances_with_start_phase():
    """current_phase reflects the index passed to start_phase."""
    svc = _make_service()
    svc.create_progress("exec-idx", total_phases=3)
    svc.start_execution("exec-idx")

    for i, name in enumerate(["Alpha", "Beta", "Gamma"]):
        svc.start_phase("exec-idx", i, name)
        assert svc.get_progress("exec-idx").current_phase == i


@pytest.mark.unit
def test_elapsed_time_updated():
    """update_elapsed_time stores elapsed and estimates remaining."""
    svc = _make_service()
    svc.create_progress("exec-time", total_phases=4)
    svc.start_execution("exec-time")
    svc.start_phase("exec-time", 0, "A")
    svc.complete_phase("exec-time", "A")  # 25% done

    svc.update_elapsed_time("exec-time", 1000.0)
    p = svc.get_progress("exec-time")
    assert p.elapsed_time == 1000.0
    # 25% done in 1000ms → remaining estimate ~3000ms
    assert p.estimated_remaining > 0
