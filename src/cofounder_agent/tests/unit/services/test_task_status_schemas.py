"""
Unit tests for task_status_schemas.py

Tests field validation, validators, and model behaviour for task status schemas.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from schemas.task_status_schemas import (
    TaskStatusFilterRequest,
    TaskStatusHistoryEntry,
    TaskStatusInfo,
    TaskStatusStatistics,
    TaskStatusUpdateRequest,
    TaskStatusUpdateResponse,
)

# ---------------------------------------------------------------------------
# TaskStatusUpdateRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusUpdateRequest:
    def test_valid_status(self):
        req = TaskStatusUpdateRequest(status="pending")  # type: ignore[call-arg]
        assert req.status == "pending"

    def test_status_normalised_to_lowercase(self):
        req = TaskStatusUpdateRequest(status="PENDING")  # type: ignore[call-arg]
        assert req.status == "pending"

    def test_all_valid_statuses(self):
        valid = [
            "pending",
            "in_progress",
            "awaiting_approval",
            "approved",
            "published",
            "failed",
            "on_hold",
            "rejected",
            "cancelled",
        ]
        for status in valid:
            req = TaskStatusUpdateRequest(status=status)  # type: ignore[call-arg]
            assert req.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateRequest(status="running")  # type: ignore[call-arg]

    def test_status_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateRequest(status="x" * 51)  # type: ignore[call-arg]

    def test_status_empty_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateRequest(status="")  # type: ignore[call-arg]

    def test_optional_fields_default_to_none(self):
        req = TaskStatusUpdateRequest(status="pending")  # type: ignore[call-arg]
        assert req.updated_by is None
        assert req.reason is None
        assert req.metadata is None

    def test_with_reason(self):
        req = TaskStatusUpdateRequest(  # type: ignore[call-arg]
            status="on_hold",
            reason="Waiting for human review",
            updated_by="admin",
        )
        assert req.reason == "Waiting for human review"
        assert req.updated_by == "admin"

    def test_reason_too_long_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateRequest(status="pending", reason="x" * 501)  # type: ignore[call-arg]

    def test_with_metadata(self):
        req = TaskStatusUpdateRequest(  # type: ignore[call-arg]
            status="failed",
            metadata={"error_code": "TIMEOUT", "retries": 3},
        )
        assert req.metadata == {"error_code": "TIMEOUT", "retries": 3}


# ---------------------------------------------------------------------------
# TaskStatusUpdateResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusUpdateResponse:
    def test_valid(self):
        resp = TaskStatusUpdateResponse(
            task_id="task-123",
            old_status="pending",
            new_status="in_progress",
            timestamp=datetime.now(timezone.utc),
        )
        assert resp.message == "Status updated successfully"
        assert resp.updated_by is None

    def test_with_updated_by(self):
        resp = TaskStatusUpdateResponse(
            task_id="task-456",
            old_status="in_progress",
            new_status="approved",
            timestamp=datetime.now(timezone.utc),
            updated_by="reviewer@example.com",
        )
        assert resp.updated_by == "reviewer@example.com"

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusUpdateResponse(  # type: ignore[call-arg]
                task_id="task-123",
                # missing old_status
                new_status="in_progress",
                timestamp=datetime.now(timezone.utc),
            )


# ---------------------------------------------------------------------------
# TaskStatusHistoryEntry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusHistoryEntry:
    def test_valid(self):
        entry = TaskStatusHistoryEntry(
            id=1,
            task_id="task-123",
            new_status="in_progress",
            changed_at=datetime.now(timezone.utc),
        )
        assert entry.old_status is None
        assert entry.changed_by is None
        assert entry.reason is None
        assert entry.metadata is None

    def test_with_all_fields(self):
        entry = TaskStatusHistoryEntry(
            id=2,
            task_id="task-456",
            old_status="pending",
            new_status="approved",
            changed_at=datetime.now(timezone.utc),
            changed_by="admin",
            reason="Quality check passed",
            metadata={"score": 95},
        )
        assert entry.old_status == "pending"
        assert entry.reason == "Quality check passed"


# ---------------------------------------------------------------------------
# TaskStatusInfo
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusInfo:
    def _valid(self, **kwargs):
        defaults = {
            "task_id": "task-123",
            "current_status": "awaiting_approval",
            "status_updated_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
            "is_terminal": False,
            "allowed_transitions": ["approved", "rejected", "cancelled"],
        }
        defaults.update(kwargs)
        return TaskStatusInfo(**defaults)

    def test_valid(self):
        info = self._valid()
        assert info.is_terminal is False
        assert "approved" in info.allowed_transitions
        assert info.status_updated_by is None
        assert info.started_at is None
        assert info.completed_at is None
        assert info.duration_minutes is None

    def test_terminal_status(self):
        info = self._valid(
            current_status="published",
            is_terminal=True,
            allowed_transitions=[],
        )
        assert info.is_terminal is True
        assert info.allowed_transitions == []

    def test_with_all_optional_fields(self):
        info = self._valid(
            status_updated_by="admin",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            duration_minutes=25.5,
        )
        assert info.duration_minutes == 25.5
        assert info.status_updated_by == "admin"


# ---------------------------------------------------------------------------
# TaskStatusFilterRequest
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusFilterRequest:
    def test_valid(self):
        req = TaskStatusFilterRequest(statuses=["pending", "in_progress"])  # type: ignore[call-arg]
        assert req.limit == 100
        assert req.offset == 0
        assert req.sort_by == "created_at"
        assert req.sort_order == "desc"

    def test_statuses_normalised_to_lowercase(self):
        req = TaskStatusFilterRequest(statuses=["PENDING", "IN_PROGRESS"])  # type: ignore[call-arg]
        assert req.statuses == ["pending", "in_progress"]

    def test_invalid_status_in_list_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest(statuses=["pending", "unknown_status"])  # type: ignore[call-arg]

    def test_limit_bounds(self):
        req = TaskStatusFilterRequest(statuses=["pending"], limit=1)  # type: ignore[call-arg]
        assert req.limit == 1
        req = TaskStatusFilterRequest(statuses=["pending"], limit=1000)  # type: ignore[call-arg]
        assert req.limit == 1000

    def test_limit_too_low_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest(statuses=["pending"], limit=0)  # type: ignore[call-arg]

    def test_limit_too_high_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest(statuses=["pending"], limit=1001)  # type: ignore[call-arg]

    def test_offset_negative_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest(statuses=["pending"], offset=-1)  # type: ignore[call-arg]

    def test_invalid_sort_order_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest(statuses=["pending"], sort_order="random")  # type: ignore[call-arg]

    def test_valid_sort_orders(self):
        for order in ["asc", "desc"]:
            req = TaskStatusFilterRequest(statuses=["pending"], sort_order=order)  # type: ignore[call-arg]
            assert req.sort_order == order

    def test_sort_order_case_normalised(self):
        req = TaskStatusFilterRequest(statuses=["pending"], sort_order="ASC")  # type: ignore[call-arg]
        assert req.sort_order == "asc"

    def test_missing_statuses_raises(self):
        with pytest.raises(ValidationError):
            TaskStatusFilterRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# TaskStatusStatistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskStatusStatistics:
    def test_valid(self):
        stats = TaskStatusStatistics(  # type: ignore[call-arg]
            total_tasks=100,
            by_status={"pending": 20, "in_progress": 10, "completed": 70},
        )
        assert stats.total_tasks == 100
        assert stats.average_duration_minutes is None
        assert stats.oldest_task_days is None
        assert stats.recent_changes_count is None

    def test_with_all_fields(self):
        stats = TaskStatusStatistics(
            total_tasks=50,
            by_status={"pending": 5, "approved": 45},
            average_duration_minutes=15.5,
            oldest_task_days=30,
            recent_changes_count=12,
        )
        assert stats.average_duration_minutes == 15.5
        assert stats.recent_changes_count == 12
