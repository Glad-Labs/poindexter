"""Task status enumeration and validation utilities.

Provides enterprise-level task status management with:
- Comprehensive status enum
- Valid transition rules
- Terminal state tracking
- Helper functions for UI and validation
"""

from enum import Enum
from typing import Dict, Set, Optional


class TaskStatus(str, Enum):
    """Task lifecycle statuses - enterprise-level task management."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        """Return string value."""
        return self.value

    def __repr__(self) -> str:
        """Return representation."""
        return f"TaskStatus.{self.name}"


# Valid status transitions - defines workflow rules
VALID_TRANSITIONS: Dict[TaskStatus, Set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.CANCELLED,
        TaskStatus.FAILED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.ON_HOLD,
        TaskStatus.CANCELLED,
    },
    TaskStatus.AWAITING_APPROVAL: {
        TaskStatus.APPROVED,
        TaskStatus.REJECTED,
        TaskStatus.IN_PROGRESS,  # Back for rework
        TaskStatus.CANCELLED,
    },
    TaskStatus.APPROVED: {
        TaskStatus.PUBLISHED,
        TaskStatus.ON_HOLD,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PUBLISHED: {TaskStatus.ON_HOLD},  # Terminal, except pause
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.CANCELLED},  # Retry or give up
    TaskStatus.ON_HOLD: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.REJECTED: {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.CANCELLED: set(),  # Terminal - no transitions
}

# Terminal states (no further processing without manual intervention)
TERMINAL_STATUSES: Set[TaskStatus] = {
    TaskStatus.PUBLISHED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

# Active processing statuses (task is being worked on)
ACTIVE_STATUSES: Set[TaskStatus] = {
    TaskStatus.PENDING,
    TaskStatus.IN_PROGRESS,
    TaskStatus.ON_HOLD,
}

# Status descriptions for UI
STATUS_DESCRIPTIONS: Dict[TaskStatus, str] = {
    TaskStatus.PENDING: "Waiting to start processing",
    TaskStatus.IN_PROGRESS: "Currently being processed",
    TaskStatus.AWAITING_APPROVAL: "Waiting for human review and approval",
    TaskStatus.APPROVED: "Approved and ready for publishing",
    TaskStatus.PUBLISHED: "Published and live",
    TaskStatus.FAILED: "Processing failed - requires intervention",
    TaskStatus.ON_HOLD: "Temporarily paused",
    TaskStatus.REJECTED: "Approval rejected - requires rework",
    TaskStatus.CANCELLED: "Cancelled - no further action",
}

# Frontend color mapping
STATUS_COLORS: Dict[TaskStatus, str] = {
    TaskStatus.PENDING: "#ffc107",  # Yellow
    TaskStatus.IN_PROGRESS: "#2196f3",  # Blue
    TaskStatus.AWAITING_APPROVAL: "#ff9800",  # Orange
    TaskStatus.APPROVED: "#9c27b0",  # Purple
    TaskStatus.PUBLISHED: "#4caf50",  # Green
    TaskStatus.FAILED: "#f44336",  # Red
    TaskStatus.ON_HOLD: "#9e9e9e",  # Gray
    TaskStatus.REJECTED: "#ff5722",  # Red-Orange
    TaskStatus.CANCELLED: "#616161",  # Dark Gray
}

# CSS class names for status badges
STATUS_CSS_CLASSES: Dict[TaskStatus, str] = {
    TaskStatus.PENDING: "status-pending",
    TaskStatus.IN_PROGRESS: "status-in-progress",
    TaskStatus.AWAITING_APPROVAL: "status-awaiting-approval",
    TaskStatus.APPROVED: "status-approved",
    TaskStatus.PUBLISHED: "status-published",
    TaskStatus.FAILED: "status-failed",
    TaskStatus.ON_HOLD: "status-on-hold",
    TaskStatus.REJECTED: "status-rejected",
    TaskStatus.CANCELLED: "status-cancelled",
}

# Status icons for UI
STATUS_ICONS: Dict[TaskStatus, str] = {
    TaskStatus.PENDING: "⧗",  # Hourglass
    TaskStatus.IN_PROGRESS: "⟳",  # Refresh/spinning
    TaskStatus.AWAITING_APPROVAL: "⚠",  # Warning
    TaskStatus.APPROVED: "✓",  # Check
    TaskStatus.PUBLISHED: "✓✓",  # Double check
    TaskStatus.FAILED: "✗",  # X
    TaskStatus.ON_HOLD: "⊥",  # Pause
    TaskStatus.REJECTED: "✗",  # X
    TaskStatus.CANCELLED: "⊙",  # Circle
}


def is_valid_transition(
    current_status: TaskStatus,
    target_status: TaskStatus,
) -> bool:
    """Check if status transition is allowed.

    Args:
        current_status: Current task status
        target_status: Target task status

    Returns:
        True if transition is valid, False otherwise
    """
    if current_status == target_status:
        return True  # Allow "updating" to same status
    return target_status in VALID_TRANSITIONS.get(current_status, set())


def get_allowed_transitions(status: TaskStatus) -> Set[str]:
    """Get list of allowed status transitions for UI dropdown.

    Args:
        status: Current task status

    Returns:
        Set of valid status values that can be transitioned to
    """
    transitions = VALID_TRANSITIONS.get(status, set())
    return {s.value for s in transitions}


def is_terminal(status: TaskStatus) -> bool:
    """Check if status is terminal (no further transitions allowed).

    Terminal statuses require manual intervention to change (admin override).

    Args:
        status: Task status to check

    Returns:
        True if status is terminal, False otherwise
    """
    return status in TERMINAL_STATUSES


def is_active(status: TaskStatus) -> bool:
    """Check if status indicates active processing.

    Args:
        status: Task status to check

    Returns:
        True if task is actively being processed, False otherwise
    """
    return status in ACTIVE_STATUSES


def validate_status(status_str: str) -> TaskStatus:
    """Validate and convert string to TaskStatus enum.

    Args:
        status_str: String representation of status

    Returns:
        TaskStatus enum value

    Raises:
        ValueError: If status_str is not a valid status
    """
    try:
        return TaskStatus(status_str.lower())
    except ValueError:
        valid_statuses = [s.value for s in TaskStatus]
        raise ValueError(
            f"Invalid status '{status_str}'. Must be one of: {', '.join(valid_statuses)}"
        )


def get_status_color(status: TaskStatus) -> str:
    """Get hex color for status badge.

    Args:
        status: Task status

    Returns:
        Hex color code for frontend rendering
    """
    return STATUS_COLORS.get(status, "#999999")


def get_status_css_class(status: TaskStatus) -> str:
    """Get CSS class name for status badge.

    Args:
        status: Task status

    Returns:
        CSS class name for styling
    """
    return STATUS_CSS_CLASSES.get(status, "status-default")


def get_status_icon(status: TaskStatus) -> str:
    """Get icon character for status badge.

    Args:
        status: Task status

    Returns:
        Unicode character for display
    """
    return STATUS_ICONS.get(status, "○")


def get_status_description(status: TaskStatus) -> str:
    """Get human-readable description of status.

    Args:
        status: Task status

    Returns:
        Description string for UI tooltips
    """
    return STATUS_DESCRIPTIONS.get(status, "Unknown status")


def transition_with_validation(
    current_status: TaskStatus,
    target_status: TaskStatus,
) -> bool:
    """Validate and attempt status transition with detailed error info.

    This function is useful for providing detailed error messages
    when a transition is not allowed.

    Args:
        current_status: Current task status
        target_status: Desired target status

    Returns:
        True if transition is valid

    Raises:
        ValueError: With detailed message if transition is invalid
    """
    if current_status == target_status:
        return True

    if not is_valid_transition(current_status, target_status):
        allowed = get_allowed_transitions(current_status)
        raise ValueError(
            f"Cannot transition from {current_status.value} to {target_status.value}. "
            f"Allowed transitions: {', '.join(sorted(allowed))}"
        )

    return True


# ============================================================================
# STATUS TRANSITION VALIDATOR WITH HISTORY TRACKING
# ============================================================================

import logging
from datetime import datetime
from typing import List, Any, Tuple

logger = logging.getLogger(__name__)


class StatusTransitionValidator:
    """Validates status transitions with comprehensive error tracking."""

    def __init__(self):
        """Initialize validator."""
        self.last_validation_errors: List[str] = []
        self.transition_history: List[Dict[str, Any]] = []

    def validate_transition(
        self,
        current_status: str,
        new_status: str,
        task_id: Optional[str] = None,
        additional_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Validate a status transition with comprehensive error tracking.

        Args:
            current_status: Current task status
            new_status: Desired new status
            task_id: Task ID (for logging context)
            additional_context: Additional validation context

        Returns:
            Tuple of (is_valid, errors_list)
        """
        errors = []

        # Validate status values exist
        try:
            TaskStatus(current_status)
        except ValueError:
            errors.append(f"Invalid current status: {current_status}")

        try:
            TaskStatus(new_status)
        except ValueError:
            errors.append(f"Invalid new status: {new_status}")

        if errors:
            self.last_validation_errors = errors
            return False, errors

        # Check if transition is allowed
        if not is_valid_transition(TaskStatus(current_status), TaskStatus(new_status)):
            error_msg = f"Invalid transition: {current_status} → {new_status}"
            errors.append(error_msg)
            logger.warning(f"❌ {error_msg} (task_id: {task_id})")

        # Perform additional context validation if provided
        if additional_context:
            context_errors = self._validate_context(current_status, new_status, additional_context)
            errors.extend(context_errors)

        # Record transition attempt for audit
        self.transition_history.append(
            {
                "task_id": task_id,
                "from_status": current_status,
                "to_status": new_status,
                "timestamp": datetime.utcnow().isoformat(),
                "is_valid": len(errors) == 0,
                "errors": errors,
            }
        )

        self.last_validation_errors = errors
        return len(errors) == 0, errors

    def _validate_context(
        self, from_status: str, to_status: str, context: Dict[str, Any]
    ) -> List[str]:
        """
        Validate additional context for specific transitions.

        Args:
            from_status: Current status
            to_status: Target status
            context: Additional validation context

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Approval transition must have approval context
        if to_status == TaskStatus.AWAITING_APPROVAL.value:
            if "approval_type" not in context:
                errors.append("Transition to awaiting_approval requires approval_type")

        # Reject transition must have rejection reason
        if to_status == TaskStatus.REJECTED.value:
            if not context.get("reason"):
                errors.append("Transition to rejected requires reason")

        # Completed/published transition requires result
        if to_status in [TaskStatus.PUBLISHED.value]:
            if "result" not in context and "result_summary" not in context:
                errors.append("Transition to published requires result or result_summary")

        return errors

    def get_transition_history(self) -> List[Dict[str, Any]]:
        """Get all recorded transition attempts."""
        return self.transition_history

    def get_last_errors(self) -> List[str]:
        """Get last validation errors."""
        return self.last_validation_errors

    def clear_history(self) -> None:
        """Clear transition history."""
        self.transition_history = []
        self.last_validation_errors = []
