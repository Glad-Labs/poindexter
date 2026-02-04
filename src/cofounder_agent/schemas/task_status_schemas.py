"""Pydantic models for task status management.

Enterprise-level schemas for request/response validation
with comprehensive status handling.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class TaskStatusUpdateRequest(BaseModel):
    """Request model for updating task status with validation."""

    status: str = Field(
        ...,
        description="Target task status (pending, in_progress, awaiting_approval, approved, published, failed, on_hold, rejected, cancelled)",
        min_length=1,
        max_length=50,
    )
    updated_by: Optional[str] = Field(
        None,
        description="User/system identifier making the change",
    )
    reason: Optional[str] = Field(
        None,
        description="Reason for status change (for audit trail)",
        max_length=500,
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata for the status change",
    )

    @validator("status")
    def validate_status(cls, v):
        """Validate status is a known value."""
        valid_statuses = [
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
        if v.lower() not in valid_statuses:
            raise ValueError(f"Invalid status: {v}. Must be one of {valid_statuses}")
        return v.lower()


class TaskStatusUpdateResponse(BaseModel):
    """Response model for successful status update."""

    task_id: str
    old_status: str
    new_status: str
    timestamp: datetime
    updated_by: Optional[str] = None
    message: str = "Status updated successfully"


class TaskStatusHistoryEntry(BaseModel):
    """Model for task status history record."""

    id: int
    task_id: str
    old_status: Optional[str] = None
    new_status: str
    changed_at: datetime
    changed_by: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskStatusInfo(BaseModel):
    """Detailed information about a task's current status."""

    task_id: str
    current_status: str
    status_updated_at: datetime
    status_updated_by: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    is_terminal: bool = Field(
        ...,
        description="Whether status is terminal (no further transitions without override)",
    )
    allowed_transitions: List[str] = Field(
        ...,
        description="List of valid status values this task can transition to",
    )
    duration_minutes: Optional[float] = Field(
        None,
        description="Minutes elapsed since status change",
    )

    class Config:
        """Pydantic config."""

        schema_extra = {
            "example": {
                "task_id": "550e8400-e29b-41d4-a716-446655440000",
                "current_status": "awaiting_approval",
                "status_updated_at": "2026-01-16T10:30:00Z",
                "status_updated_by": "user@example.com",
                "created_at": "2026-01-16T10:00:00Z",
                "started_at": "2026-01-16T10:05:00Z",
                "completed_at": None,
                "is_terminal": False,
                "allowed_transitions": ["approved", "rejected", "in_progress", "cancelled"],
                "duration_minutes": 25.5,
            }
        }


class TaskStatusFilterRequest(BaseModel):
    """Request model for filtering tasks by status."""

    statuses: List[str] = Field(
        ...,
        description="List of statuses to filter by",
        min_items=1,
    )
    limit: int = Field(
        100,
        description="Maximum number of results to return",
        ge=1,
        le=1000,
    )
    offset: int = Field(
        0,
        description="Offset for pagination",
        ge=0,
    )
    sort_by: Optional[str] = Field(
        "created_at",
        description="Field to sort by (created_at, updated_at, status_updated_at)",
    )
    sort_order: Optional[str] = Field(
        "desc",
        description="Sort order (asc or desc)",
    )

    @validator("statuses")
    def validate_statuses(cls, v):
        """Validate all statuses are valid."""
        valid_statuses = {
            "pending",
            "in_progress",
            "awaiting_approval",
            "approved",
            "published",
            "failed",
            "on_hold",
            "rejected",
            "cancelled",
        }
        for status in v:
            if status.lower() not in valid_statuses:
                raise ValueError(f"Invalid status: {status}")
        return [s.lower() for s in v]

    @validator("sort_order")
    def validate_sort_order(cls, v):
        """Validate sort order."""
        if v.lower() not in ["asc", "desc"]:
            raise ValueError("sort_order must be 'asc' or 'desc'")
        return v.lower()


class TaskStatusStatistics(BaseModel):
    """Statistics about task statuses."""

    total_tasks: int
    by_status: Dict[str, int] = Field(
        ...,
        description="Count of tasks by status",
    )
    average_duration_minutes: Optional[float] = None
    oldest_task_days: Optional[int] = None
    recent_changes_count: Optional[int] = Field(
        None,
        description="Number of status changes in last 24 hours",
    )
