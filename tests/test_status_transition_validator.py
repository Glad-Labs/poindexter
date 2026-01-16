"""Unit tests for StatusTransitionValidator."""

import pytest
from datetime import datetime

from src.cofounder_agent.utils.task_status import (
    StatusTransitionValidator,
    TaskStatus,
    is_valid_transition
)


class TestStatusTransitionValidator:
    """Test suite for StatusTransitionValidator."""

    @pytest.fixture
    def validator(self):
        """Create validator instance for testing."""
        return StatusTransitionValidator()

    def test_validator_initialization(self, validator):
        """Test validator initializes with empty history."""
        assert validator.last_validation_errors == []
        assert validator.transition_history == []

    def test_valid_transition_pending_to_in_progress(self, validator):
        """Test valid transition from pending to in_progress."""
        is_valid, errors = validator.validate_transition(
            current_status="pending",
            new_status="in_progress",
            task_id="task-1"
        )

        assert is_valid is True
        assert errors == []
        assert len(validator.transition_history) == 1
        assert validator.transition_history[0]["is_valid"] is True

    def test_valid_transition_in_progress_to_awaiting_approval(self, validator):
        """Test valid transition from in_progress to awaiting_approval."""
        is_valid, errors = validator.validate_transition(
            current_status="in_progress",
            new_status="awaiting_approval",
            task_id="task-1"
        )

        assert is_valid is True
        assert errors == []

    def test_invalid_transition_pending_to_published(self, validator):
        """Test invalid transition from pending directly to published."""
        is_valid, errors = validator.validate_transition(
            current_status="pending",
            new_status="published",
            task_id="task-1"
        )

        assert is_valid is False
        assert len(errors) > 0
        assert "invalid_transition" in str(errors[0]).lower() or \
               "cannot transition" in str(errors[0]).lower()

    def test_invalid_status_value(self, validator):
        """Test validation rejects invalid status values."""
        is_valid, errors = validator.validate_transition(
            current_status="invalid_status",
            new_status="pending",
            task_id="task-1"
        )

        assert is_valid is False
        assert len(errors) > 0
        assert "invalid" in str(errors[0]).lower()

    def test_awaiting_approval_requires_approval_type(self, validator):
        """Test context validation for awaiting_approval transition."""
        is_valid, errors = validator.validate_transition(
            current_status="in_progress",
            new_status="awaiting_approval",
            task_id="task-1",
            additional_context={}  # No approval_type provided
        )

        # The transition itself is valid, but context validation fails
        assert is_valid is False
        assert any("approval_type" in err for err in errors)

    def test_awaiting_approval_with_approval_type(self, validator):
        """Test context validation passes with approval_type."""
        is_valid, errors = validator.validate_transition(
            current_status="in_progress",
            new_status="awaiting_approval",
            task_id="task-1",
            additional_context={"approval_type": "editorial"}
        )

        assert is_valid is True
        assert errors == []

    def test_rejected_requires_reason(self, validator):
        """Test context validation requires reason for rejection."""
        is_valid, errors = validator.validate_transition(
            current_status="awaiting_approval",
            new_status="rejected",
            task_id="task-1",
            additional_context={}  # No reason provided
        )

        assert is_valid is False
        assert any("reason" in err for err in errors)

    def test_rejected_with_reason(self, validator):
        """Test rejection with proper reason."""
        is_valid, errors = validator.validate_transition(
            current_status="awaiting_approval",
            new_status="rejected",
            task_id="task-1",
            additional_context={"reason": "Content quality below threshold"}
        )

        assert is_valid is True
        assert errors == []

    def test_transition_history_tracking(self, validator):
        """Test validator tracks all transition attempts."""
        # Attempt multiple transitions
        validator.validate_transition("pending", "in_progress", "task-1")
        validator.validate_transition("invalid", "pending", "task-1")
        validator.validate_transition("in_progress", "awaiting_approval", "task-1")

        history = validator.get_transition_history()
        assert len(history) == 3
        assert history[0]["is_valid"] is True
        assert history[1]["is_valid"] is False
        assert history[2]["is_valid"] is True

    def test_last_errors_tracking(self, validator):
        """Test validator tracks last validation errors."""
        validator.validate_transition("invalid_status", "pending")
        errors = validator.get_last_errors()
        assert len(errors) > 0
        assert "invalid" in str(errors[0]).lower()

    def test_published_requires_result(self, validator):
        """Test published transition context validation."""
        is_valid, errors = validator.validate_transition(
            current_status="approved",
            new_status="published",
            task_id="task-1",
            additional_context={}  # No result
        )

        assert is_valid is False
        assert any("result" in err.lower() for err in errors)

    def test_published_with_result(self, validator):
        """Test published transition with result."""
        is_valid, errors = validator.validate_transition(
            current_status="approved",
            new_status="published",
            task_id="task-1",
            additional_context={"result": {"url": "https://example.com/post"}}
        )

        assert is_valid is True
        assert errors == []

    def test_clear_history(self, validator):
        """Test clearing history."""
        validator.validate_transition("pending", "in_progress", "task-1")
        assert len(validator.transition_history) > 0

        validator.clear_history()
        assert validator.transition_history == []
        assert validator.last_validation_errors == []

    def test_workflow_sequence_valid(self, validator):
        """Test a complete valid workflow sequence."""
        transitions = [
            ("pending", "in_progress"),
            ("in_progress", "awaiting_approval", {"approval_type": "editorial"}),
            ("awaiting_approval", "approved", {}),
            ("approved", "published", {"result": {"url": "post-url"}})
        ]

        for transition in transitions:
            current, target = transition[0], transition[1]
            context = transition[2] if len(transition) > 2 else {}

            is_valid, errors = validator.validate_transition(
                current_status=current,
                new_status=target,
                task_id="task-workflow",
                additional_context=context
            )

            assert is_valid is True, f"Transition {current} → {target} failed: {errors}"

    def test_all_terminal_transitions(self, validator):
        """Test transitions from terminal states."""
        # cancelled is terminal - no transitions allowed
        is_valid, errors = validator.validate_transition(
            current_status="cancelled",
            new_status="pending",
            task_id="task-1"
        )

        assert is_valid is False

    def test_retry_after_failure(self, validator):
        """Test retry workflow after failure."""
        # Start pending
        is_valid, _ = validator.validate_transition("pending", "in_progress", "task-1")
        assert is_valid is True

        # Fail
        is_valid, _ = validator.validate_transition("in_progress", "failed", "task-1")
        assert is_valid is True

        # Retry from failure
        is_valid, _ = validator.validate_transition("failed", "pending", "task-1")
        assert is_valid is True

        # Start again
        is_valid, _ = validator.validate_transition("pending", "in_progress", "task-1")
        assert is_valid is True


class TestTaskStatusEnum:
    """Test TaskStatus enum values and helpers."""

    def test_all_statuses_exist(self):
        """Test all expected status values exist."""
        expected = [
            "pending", "in_progress", "awaiting_approval", "approved",
            "published", "failed", "on_hold", "rejected", "cancelled"
        ]

        for status in expected:
            assert hasattr(TaskStatus, status.upper())

    def test_valid_transitions_accessible(self):
        """Test valid transitions can be retrieved."""
        transitions = TaskStatus.get_valid_transitions()
        assert isinstance(transitions, dict)
        assert len(transitions) > 0

    def test_is_valid_transition_works(self):
        """Test is_valid_transition function."""
        assert is_valid_transition(TaskStatus.PENDING, TaskStatus.IN_PROGRESS) is True
        assert is_valid_transition(TaskStatus.PENDING, TaskStatus.PUBLISHED) is False
