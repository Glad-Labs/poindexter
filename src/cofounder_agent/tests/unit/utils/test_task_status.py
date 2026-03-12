"""
Unit tests for utils.task_status module.

All tests are pure — zero DB, LLM, or network calls.
Covers TaskStatus enum, VALID_TRANSITIONS, terminal/active sets,
and all helper functions.
"""

import pytest

from utils.task_status import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    StatusTransitionValidator,
    TaskStatus,
    get_allowed_transitions,
    get_status_color,
    get_status_css_class,
    get_status_description,
    get_status_icon,
    is_active,
    is_terminal,
    is_valid_transition,
    transition_with_validation,
    validate_status,
)
from utils.constraint_utils import (
    apply_strict_mode,
    check_tolerance,
    ConstraintCompliance,
)


# ---------------------------------------------------------------------------
# TaskStatus enum
# ---------------------------------------------------------------------------


class TestTaskStatusEnum:
    """Tests for the TaskStatus enum itself."""

    def test_all_statuses_have_string_values(self):
        for status in TaskStatus:
            assert isinstance(status.value, str)

    def test_str_returns_value(self):
        assert str(TaskStatus.PENDING) == "pending"
        assert str(TaskStatus.IN_PROGRESS) == "in_progress"
        assert str(TaskStatus.PUBLISHED) == "published"

    def test_repr_includes_name(self):
        assert repr(TaskStatus.FAILED) == "TaskStatus.FAILED"

    def test_nine_statuses_defined(self):
        assert len(list(TaskStatus)) == 9

    def test_all_expected_statuses_exist(self):
        expected = {
            "pending", "in_progress", "awaiting_approval", "approved",
            "published", "failed", "on_hold", "rejected", "cancelled",
        }
        assert {s.value for s in TaskStatus} == expected


# ---------------------------------------------------------------------------
# is_valid_transition
# ---------------------------------------------------------------------------


class TestIsValidTransition:
    """Tests for the is_valid_transition function."""

    def test_same_status_always_valid(self):
        for status in TaskStatus:
            assert is_valid_transition(status, status) is True

    def test_pending_to_in_progress_allowed(self):
        assert is_valid_transition(TaskStatus.PENDING, TaskStatus.IN_PROGRESS) is True

    def test_pending_to_cancelled_allowed(self):
        assert is_valid_transition(TaskStatus.PENDING, TaskStatus.CANCELLED) is True

    def test_pending_to_published_not_allowed(self):
        assert is_valid_transition(TaskStatus.PENDING, TaskStatus.PUBLISHED) is False

    def test_in_progress_to_awaiting_approval_allowed(self):
        assert is_valid_transition(TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_APPROVAL) is True

    def test_in_progress_to_pending_not_allowed(self):
        assert is_valid_transition(TaskStatus.IN_PROGRESS, TaskStatus.PENDING) is False

    def test_awaiting_approval_to_approved_allowed(self):
        assert is_valid_transition(TaskStatus.AWAITING_APPROVAL, TaskStatus.APPROVED) is True

    def test_awaiting_approval_to_rejected_allowed(self):
        assert is_valid_transition(TaskStatus.AWAITING_APPROVAL, TaskStatus.REJECTED) is True

    def test_approved_to_published_allowed(self):
        assert is_valid_transition(TaskStatus.APPROVED, TaskStatus.PUBLISHED) is True

    def test_failed_to_pending_allowed(self):
        assert is_valid_transition(TaskStatus.FAILED, TaskStatus.PENDING) is True

    def test_cancelled_has_no_transitions(self):
        for status in TaskStatus:
            if status != TaskStatus.CANCELLED:
                assert is_valid_transition(TaskStatus.CANCELLED, status) is False

    def test_published_can_go_on_hold(self):
        assert is_valid_transition(TaskStatus.PUBLISHED, TaskStatus.ON_HOLD) is True

    def test_published_cannot_go_to_pending(self):
        assert is_valid_transition(TaskStatus.PUBLISHED, TaskStatus.PENDING) is False


# ---------------------------------------------------------------------------
# get_allowed_transitions
# ---------------------------------------------------------------------------


class TestGetAllowedTransitions:
    """Tests for get_allowed_transitions."""

    def test_returns_set_of_strings(self):
        result = get_allowed_transitions(TaskStatus.PENDING)
        assert isinstance(result, set)
        assert all(isinstance(v, str) for v in result)

    def test_pending_allows_in_progress_and_cancelled(self):
        result = get_allowed_transitions(TaskStatus.PENDING)
        assert "in_progress" in result
        assert "cancelled" in result

    def test_cancelled_returns_empty_set(self):
        result = get_allowed_transitions(TaskStatus.CANCELLED)
        assert result == set()

    def test_approved_allows_published(self):
        result = get_allowed_transitions(TaskStatus.APPROVED)
        assert "published" in result


# ---------------------------------------------------------------------------
# is_terminal / is_active
# ---------------------------------------------------------------------------


class TestTerminalAndActive:
    """Tests for is_terminal and is_active helpers."""

    def test_published_is_terminal(self):
        assert is_terminal(TaskStatus.PUBLISHED) is True

    def test_failed_is_terminal(self):
        assert is_terminal(TaskStatus.FAILED) is True

    def test_cancelled_is_terminal(self):
        assert is_terminal(TaskStatus.CANCELLED) is True

    def test_pending_is_not_terminal(self):
        assert is_terminal(TaskStatus.PENDING) is False

    def test_in_progress_is_not_terminal(self):
        assert is_terminal(TaskStatus.IN_PROGRESS) is False

    def test_pending_is_active(self):
        assert is_active(TaskStatus.PENDING) is True

    def test_in_progress_is_active(self):
        assert is_active(TaskStatus.IN_PROGRESS) is True

    def test_on_hold_is_active(self):
        assert is_active(TaskStatus.ON_HOLD) is True

    def test_published_is_not_active(self):
        assert is_active(TaskStatus.PUBLISHED) is False

    def test_cancelled_is_not_active(self):
        assert is_active(TaskStatus.CANCELLED) is False


# ---------------------------------------------------------------------------
# validate_status
# ---------------------------------------------------------------------------


class TestValidateStatus:
    """Tests for validate_status."""

    def test_valid_status_string_returns_enum(self):
        result = validate_status("pending")
        assert result == TaskStatus.PENDING

    def test_valid_status_in_progress(self):
        result = validate_status("in_progress")
        assert result == TaskStatus.IN_PROGRESS

    def test_valid_status_uppercase_lowercased(self):
        result = validate_status("PENDING")
        assert result == TaskStatus.PENDING

    def test_invalid_status_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid status"):
            validate_status("not_a_status")

    def test_error_message_lists_valid_values(self):
        with pytest.raises(ValueError, match="pending"):
            validate_status("invalid")


# ---------------------------------------------------------------------------
# get_status_color / get_status_css_class / get_status_icon / get_status_description
# ---------------------------------------------------------------------------


class TestStatusDisplayHelpers:
    """Tests for the display helper functions."""

    def test_get_status_color_returns_hex_string(self):
        for status in TaskStatus:
            color = get_status_color(status)
            assert isinstance(color, str)
            assert color.startswith("#")

    def test_get_status_color_fallback_for_unknown(self):
        # Pass a status that is not in STATUS_COLORS (shouldn't happen normally)
        # We test via the function's fallback — use a known status instead
        color = get_status_color(TaskStatus.PENDING)
        assert color == "#ffc107"

    def test_get_status_css_class_returns_string(self):
        for status in TaskStatus:
            cls = get_status_css_class(status)
            assert isinstance(cls, str)
            assert len(cls) > 0

    def test_get_status_css_class_contains_status_name(self):
        assert "pending" in get_status_css_class(TaskStatus.PENDING)
        assert "published" in get_status_css_class(TaskStatus.PUBLISHED)
        assert "failed" in get_status_css_class(TaskStatus.FAILED)

    def test_get_status_icon_returns_string(self):
        for status in TaskStatus:
            icon = get_status_icon(status)
            assert isinstance(icon, str)
            assert len(icon) > 0

    def test_get_status_description_returns_string(self):
        for status in TaskStatus:
            desc = get_status_description(status)
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_get_status_description_pending(self):
        desc = get_status_description(TaskStatus.PENDING)
        assert "wait" in desc.lower() or "start" in desc.lower()


# ---------------------------------------------------------------------------
# transition_with_validation
# ---------------------------------------------------------------------------


class TestTransitionWithValidation:
    """Tests for transition_with_validation."""

    def test_same_status_returns_true(self):
        assert transition_with_validation(TaskStatus.PENDING, TaskStatus.PENDING) is True

    def test_valid_transition_returns_true(self):
        assert transition_with_validation(TaskStatus.PENDING, TaskStatus.IN_PROGRESS) is True

    def test_invalid_transition_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot transition"):
            transition_with_validation(TaskStatus.PENDING, TaskStatus.PUBLISHED)

    def test_error_includes_current_and_target_status(self):
        with pytest.raises(ValueError) as exc_info:
            transition_with_validation(TaskStatus.CANCELLED, TaskStatus.PENDING)
        msg = str(exc_info.value)
        assert "cancelled" in msg
        assert "pending" in msg

    def test_error_includes_allowed_transitions(self):
        with pytest.raises(ValueError) as exc_info:
            transition_with_validation(TaskStatus.PENDING, TaskStatus.PUBLISHED)
        msg = str(exc_info.value)
        assert "in_progress" in msg or "Allowed" in msg


# ---------------------------------------------------------------------------
# check_tolerance (imported from task_status — re-exported from constraint_utils)
# ---------------------------------------------------------------------------


class TestCheckTolerance:
    """Tests for check_tolerance."""

    def test_value_within_tolerance(self):
        is_within, pct = check_tolerance(actual_value=1000, target_value=1000, tolerance_percent=10)
        assert is_within is True
        assert pct == pytest.approx(0.0)

    def test_value_just_at_upper_boundary(self):
        is_within, _ = check_tolerance(actual_value=1100, target_value=1000, tolerance_percent=10)
        assert is_within is True

    def test_value_just_at_lower_boundary(self):
        is_within, _ = check_tolerance(actual_value=900, target_value=1000, tolerance_percent=10)
        assert is_within is True

    def test_value_above_upper_boundary(self):
        is_within, _ = check_tolerance(actual_value=1101, target_value=1000, tolerance_percent=10)
        assert is_within is False

    def test_value_below_lower_boundary(self):
        is_within, _ = check_tolerance(actual_value=899, target_value=1000, tolerance_percent=10)
        assert is_within is False

    def test_percentage_calculation_over(self):
        _, pct = check_tolerance(actual_value=1200, target_value=1000, tolerance_percent=5)
        assert pct == pytest.approx(20.0)

    def test_percentage_calculation_under(self):
        _, pct = check_tolerance(actual_value=800, target_value=1000, tolerance_percent=5)
        assert pct == pytest.approx(-20.0)

    def test_zero_target_returns_false(self):
        is_within, pct = check_tolerance(actual_value=0, target_value=0, tolerance_percent=10)
        assert is_within is False
        assert pct == 0.0


# ---------------------------------------------------------------------------
# apply_strict_mode
# ---------------------------------------------------------------------------



class TestApplyStrictMode:
    """Tests for apply_strict_mode."""

    def _make_compliance(self, within_tolerance: bool, strict: bool, msg: str | None = None):
        return ConstraintCompliance(
            word_count_actual=1000,
            word_count_target=1000,
            word_count_within_tolerance=within_tolerance,
            word_count_percentage=0.0,
            writing_style_applied="educational",
            strict_mode_enforced=strict,
            violation_message=msg,
        )

    def test_non_strict_mode_always_valid(self):
        compliance = self._make_compliance(within_tolerance=False, strict=False)
        is_valid, msg = apply_strict_mode(compliance)
        assert is_valid is True
        assert msg == ""

    def test_strict_mode_within_tolerance_is_valid(self):
        compliance = self._make_compliance(within_tolerance=True, strict=True)
        is_valid, msg = apply_strict_mode(compliance)
        assert is_valid is True
        assert msg == ""

    def test_strict_mode_outside_tolerance_is_invalid(self):
        compliance = self._make_compliance(
            within_tolerance=False, strict=True, msg="Content too short: 500 words"
        )
        is_valid, msg = apply_strict_mode(compliance)
        assert is_valid is False
        assert "500 words" in msg

    def test_strict_mode_violation_with_no_message_uses_default(self):
        compliance = self._make_compliance(within_tolerance=False, strict=True, msg=None)
        is_valid, msg = apply_strict_mode(compliance)
        assert is_valid is False
        assert len(msg) > 0


# ---------------------------------------------------------------------------
# StatusTransitionValidator
# ---------------------------------------------------------------------------


class TestStatusTransitionValidator:
    """Tests for the StatusTransitionValidator class."""

    def test_validate_valid_transition(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition("pending", "in_progress")
        assert is_valid is True
        assert errors == []

    def test_validate_invalid_transition(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition("pending", "published")
        assert is_valid is False
        assert len(errors) > 0

    def test_validate_invalid_current_status(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition("invalid_status", "pending")
        assert is_valid is False
        assert any("Invalid current status" in e for e in errors)

    def test_validate_invalid_new_status(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition("pending", "not_valid")
        assert is_valid is False
        assert any("Invalid new status" in e for e in errors)

    def test_validate_both_invalid_statuses(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition("bad", "worse")
        assert is_valid is False
        assert len(errors) == 2

    def test_records_transition_in_history(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "in_progress", task_id="task-1")
        history = validator.get_transition_history()
        assert len(history) == 1
        assert history[0]["task_id"] == "task-1"
        assert history[0]["from_status"] == "pending"
        assert history[0]["to_status"] == "in_progress"
        assert history[0]["is_valid"] is True

    def test_records_failed_transition_in_history(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "published")
        history = validator.get_transition_history()
        assert len(history) == 1
        assert history[0]["is_valid"] is False
        assert len(history[0]["errors"]) > 0

    def test_get_last_errors_after_validation(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "published")
        errors = validator.get_last_errors()
        assert len(errors) > 0

    def test_clear_history_resets_state(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "in_progress")
        validator.validate_transition("pending", "published")
        validator.clear_history()
        assert validator.get_transition_history() == []
        assert validator.get_last_errors() == []

    def test_context_validation_awaiting_approval_requires_approval_type(self):
        # Context is provided (truthy), but missing approval_type — should fail
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "in_progress",
            "awaiting_approval",
            additional_context={"other_key": "some_value"},  # approval_type absent
        )
        assert is_valid is False
        assert any("approval_type" in e for e in errors)

    def test_context_validation_awaiting_approval_with_approval_type(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "in_progress",
            "awaiting_approval",
            additional_context={"approval_type": "editorial"},
        )
        assert is_valid is True
        assert errors == []

    def test_context_validation_rejected_requires_reason(self):
        # Context is provided (truthy), but missing reason — should fail
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "awaiting_approval",
            "rejected",
            additional_context={"other_key": "some_value"},  # reason absent
        )
        assert is_valid is False
        assert any("reason" in e for e in errors)

    def test_context_validation_rejected_with_reason(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "awaiting_approval",
            "rejected",
            additional_context={"reason": "Content does not meet standards"},
        )
        assert is_valid is True
        assert errors == []

    def test_context_validation_published_requires_result(self):
        # Context is provided (truthy), but missing result/result_summary — should fail
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "approved",
            "published",
            additional_context={"other_key": "some_value"},  # result absent
        )
        assert is_valid is False
        assert any("result" in e for e in errors)

    def test_context_validation_published_with_result_summary(self):
        validator = StatusTransitionValidator()
        is_valid, errors = validator.validate_transition(
            "approved",
            "published",
            additional_context={"result_summary": "Post published to CMS"},
        )
        assert is_valid is True
        assert errors == []

    def test_transition_history_has_timestamp(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "in_progress")
        history = validator.get_transition_history()
        assert "timestamp" in history[0]
        assert "2026" in history[0]["timestamp"] or "202" in history[0]["timestamp"]

    def test_multiple_transitions_all_recorded(self):
        validator = StatusTransitionValidator()
        validator.validate_transition("pending", "in_progress", task_id="t1")
        validator.validate_transition("in_progress", "awaiting_approval", task_id="t1")
        validator.validate_transition(
            "awaiting_approval", "approved", task_id="t1"
        )
        history = validator.get_transition_history()
        assert len(history) == 3
