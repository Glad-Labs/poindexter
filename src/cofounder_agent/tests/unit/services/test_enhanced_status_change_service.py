"""
Unit tests for services.enhanced_status_change_service.EnhancedStatusChangeService

All DB calls are mocked via AsyncMock — zero real I/O.

Tests cover:
- validate_and_change_status: task not found, invalid transition, log failure (warning), update failure,
  success path, retry metadata increment
- get_status_audit_trail: success path, DB error fallback
- get_validation_failures: success path, DB error fallback
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.enhanced_status_change_service import EnhancedStatusChangeService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(status="pending", task_metadata=None):
    return {"id": "task-1", "status": status, "task_metadata": task_metadata or {}}


def make_db(
    task=None,
    log_ok=True,
    update_ok=True,
    history=None,
    failures=None,
):
    db = MagicMock()
    db.get_task = AsyncMock(return_value=task)
    db.log_status_change = AsyncMock(return_value=log_ok)
    db.update_task = AsyncMock(return_value=update_ok)
    db.get_status_history = AsyncMock(return_value=history or [])
    db.get_validation_failures = AsyncMock(return_value=failures or [])
    return db


def make_validator(is_valid=True, errors=None):
    v = MagicMock()
    v.validate_transition = MagicMock(return_value=(is_valid, errors or []))
    return v


# ---------------------------------------------------------------------------
# validate_and_change_status
# ---------------------------------------------------------------------------


class TestValidateAndChangeStatus:
    @pytest.mark.asyncio
    async def test_task_not_found_returns_false(self):
        db = make_db(task=None)
        svc = EnhancedStatusChangeService(db)
        ok, msg, errors = await svc.validate_and_change_status("task-1", "processing")
        assert ok is False
        assert "not found" in msg
        assert "task_not_found" in errors

    @pytest.mark.asyncio
    async def test_invalid_transition_returns_false(self):
        db = make_db(task=make_task("completed"))
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=False, errors=["invalid_status"])
        ok, msg, errors = await svc.validate_and_change_status("task-1", "pending")
        assert ok is False
        assert "Invalid" in msg
        assert "invalid_status" in errors

    @pytest.mark.asyncio
    async def test_log_failure_warns_but_continues(self):
        task = make_task("pending")
        db = make_db(task=task, log_ok=False, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status("task-1", "processing")
        # Update still proceeds even if log fails
        assert ok is True

    @pytest.mark.asyncio
    async def test_update_failure_returns_false(self):
        task = make_task("pending")
        db = make_db(task=task, log_ok=True, update_ok=False)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status("task-1", "processing")
        assert ok is False
        assert "update_failed" in errors

    @pytest.mark.asyncio
    async def test_success_path_returns_true(self):
        task = make_task("pending")
        db = make_db(task=task, log_ok=True, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status("task-1", "processing")
        assert ok is True
        assert errors == []
        assert "pending" in msg
        assert "processing" in msg

    @pytest.mark.asyncio
    async def test_metadata_merged_into_update(self):
        task = make_task("pending", task_metadata={"existing": "data"})
        db = make_db(task=task, log_ok=True, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status(
            "task-1", "processing", metadata={"new_key": "new_val"}
        )
        assert ok is True
        update_call_data = db.update_task.call_args[0][1]
        assert update_call_data["task_metadata"]["existing"] == "data"
        assert update_call_data["task_metadata"]["new_key"] == "new_val"

    @pytest.mark.asyncio
    async def test_retry_metadata_increments_count(self):
        task = make_task("failed", task_metadata={"retry_count": 2})
        db = make_db(task=task, log_ok=True, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status(
            "task-1", "pending", metadata={"action": "retry"}, user_id="user-42"
        )
        assert ok is True
        update_data = db.update_task.call_args[0][1]
        assert update_data["task_metadata"]["retry_count"] == 3
        assert update_data["task_metadata"]["last_retry_by"] == "user-42"

    @pytest.mark.asyncio
    async def test_retry_count_initializes_at_zero_if_missing(self):
        task = make_task("failed", task_metadata={})
        db = make_db(task=task, log_ok=True, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        ok, msg, errors = await svc.validate_and_change_status(
            "task-1", "pending", metadata={"action": "retry"}
        )
        assert ok is True
        update_data = db.update_task.call_args[0][1]
        assert update_data["task_metadata"]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_exception_returns_internal_error(self):
        db = make_db(task=make_task())
        db.get_task = AsyncMock(side_effect=RuntimeError("db crashed"))
        svc = EnhancedStatusChangeService(db)
        ok, msg, errors = await svc.validate_and_change_status("task-1", "processing")
        assert ok is False
        assert "internal_error" in errors

    @pytest.mark.asyncio
    async def test_reason_and_user_id_logged(self):
        task = make_task("pending")
        db = make_db(task=task, log_ok=True, update_ok=True)
        svc = EnhancedStatusChangeService(db)
        svc.validator = make_validator(is_valid=True)
        await svc.validate_and_change_status(
            "task-1", "processing", reason="operator override", user_id="u-1"
        )
        log_call = db.log_status_change.call_args[1]
        assert log_call["reason"] == "operator override"
        assert log_call["metadata"]["user_id"] == "u-1"


# ---------------------------------------------------------------------------
# get_status_audit_trail
# ---------------------------------------------------------------------------


class TestGetStatusAuditTrail:
    @pytest.mark.asyncio
    async def test_returns_history(self):
        history = [{"old": "pending", "new": "processing"}]
        db = make_db(history=history)
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_status_audit_trail("task-1")
        assert result["task_id"] == "task-1"
        assert result["history_count"] == 1
        assert result["history"] == history

    @pytest.mark.asyncio
    async def test_empty_history(self):
        db = make_db(history=[])
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_status_audit_trail("task-1")
        assert result["history_count"] == 0

    @pytest.mark.asyncio
    async def test_db_error_returns_fallback(self):
        db = make_db()
        db.get_status_history = AsyncMock(side_effect=RuntimeError("db error"))
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_status_audit_trail("task-1")
        assert result["history_count"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_limit_passed_to_db(self):
        db = make_db(history=[])
        svc = EnhancedStatusChangeService(db)
        await svc.get_status_audit_trail("task-1", limit=10)
        db.get_status_history.assert_awaited_once_with("task-1", 10)


# ---------------------------------------------------------------------------
# get_validation_failures
# ---------------------------------------------------------------------------


class TestGetValidationFailures:
    @pytest.mark.asyncio
    async def test_returns_failures(self):
        failures = [{"error": "invalid_transition"}]
        db = make_db(failures=failures)
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_validation_failures("task-1")
        assert result["task_id"] == "task-1"
        assert result["failure_count"] == 1
        assert result["failures"] == failures

    @pytest.mark.asyncio
    async def test_empty_failures(self):
        db = make_db(failures=[])
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_validation_failures("task-1")
        assert result["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_db_error_returns_fallback(self):
        db = make_db()
        db.get_validation_failures = AsyncMock(side_effect=RuntimeError("db error"))
        svc = EnhancedStatusChangeService(db)
        result = await svc.get_validation_failures("task-1")
        assert result["failure_count"] == 0
        assert "error" in result

    @pytest.mark.asyncio
    async def test_limit_passed_to_db(self):
        db = make_db(failures=[])
        svc = EnhancedStatusChangeService(db)
        await svc.get_validation_failures("task-1", limit=25)
        db.get_validation_failures.assert_awaited_once_with("task-1", 25)
