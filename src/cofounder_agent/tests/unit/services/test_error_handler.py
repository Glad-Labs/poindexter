"""
Unit tests for the error_handler module.

Tests error classification, HTTP status mapping, error conversion, and the
handle_error() helper. All tests are pure-function — zero DB or network calls.
"""

import pytest
from fastapi import status

from services.error_handler import (
    AppError,
    ConflictError,
    DatabaseError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ServiceError,
    StateError,
    TimeoutError,
    UnauthorizedError,
    ValidationError,
    handle_error,
)


# ---------------------------------------------------------------------------
# AppError base class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppError:
    def test_message_is_stored(self):
        err = AppError("something went wrong")
        assert err.message == "something went wrong"
        assert str(err) == "[INTERNAL_ERROR] something went wrong"

    def test_default_error_code_is_internal_error(self):
        err = AppError("msg")
        assert err.error_code == ErrorCode.INTERNAL_ERROR

    def test_custom_error_code_is_stored(self):
        err = AppError("msg", error_code=ErrorCode.VALIDATION_ERROR)
        assert err.error_code == ErrorCode.VALIDATION_ERROR

    def test_default_http_status_is_500(self):
        err = AppError("msg")
        assert err.http_status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_details_default_to_empty_dict(self):
        err = AppError("msg")
        assert err.details == {}

    def test_details_are_stored(self):
        err = AppError("msg", details={"field": "value"})
        assert err.details == {"field": "value"}

    def test_str_includes_details(self):
        err = AppError("msg", details={"k": "v"})
        assert "Details" in str(err)

    def test_cause_is_stored(self):
        original = ValueError("root cause")
        err = AppError("wrapped", cause=original)
        assert err.cause is original

    def test_to_response_contains_error_code(self):
        err = AppError("test", error_code=ErrorCode.NOT_FOUND)
        response = err.to_response()
        assert response.error_code == ErrorCode.NOT_FOUND.value

    def test_to_http_exception_has_correct_status(self):
        err = AppError("test", http_status_code=status.HTTP_400_BAD_REQUEST)
        exc = err.to_http_exception()
        assert exc.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Subclass HTTP status mappings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubclassStatusCodes:
    def test_validation_error_is_400(self):
        err = ValidationError("bad input")
        assert err.http_status_code == status.HTTP_400_BAD_REQUEST

    def test_not_found_error_is_404(self):
        err = NotFoundError("User not found", resource_type="user", resource_id="user-42")
        assert err.http_status_code == status.HTTP_404_NOT_FOUND

    def test_unauthorized_error_is_401(self):
        err = UnauthorizedError("Not authenticated")
        assert err.http_status_code == status.HTTP_401_UNAUTHORIZED

    def test_forbidden_error_is_403(self):
        err = ForbiddenError("Access denied")
        assert err.http_status_code == status.HTTP_403_FORBIDDEN

    def test_conflict_error_is_409(self):
        err = ConflictError("Resource already exists")
        assert err.http_status_code == status.HTTP_409_CONFLICT

    def test_database_error_is_500(self):
        err = DatabaseError("connection failed")
        assert err.http_status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_service_error_is_500(self):
        err = ServiceError("llm timeout")
        assert err.http_status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_timeout_error_is_504_or_500(self):
        err = TimeoutError("llm call timed out")
        assert err.http_status_code in (
            status.HTTP_504_GATEWAY_TIMEOUT,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------------------------------------------------------------------------
# NotFoundError factory pattern
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotFoundError:
    def test_message_includes_resource_type_in_details(self):
        err = NotFoundError("task not found", resource_type="task", resource_id="task-123")
        assert err.details.get("resource_type") == "task"

    def test_resource_id_is_stored_in_details(self):
        err = NotFoundError("task not found", resource_type="task", resource_id="task-123")
        assert err.details.get("resource_id") == "task-123"


# ---------------------------------------------------------------------------
# handle_error() conversion function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleError:
    def test_app_error_is_returned_unchanged(self):
        original = ValidationError("bad field")
        result = handle_error(original, log_exception=False)
        assert result is original

    def test_generic_exception_is_converted_to_service_error(self):
        exc = RuntimeError("unexpected crash")
        result = handle_error(exc, log_exception=False)
        assert isinstance(result, ServiceError)

    def test_error_type_is_preserved_in_details(self):
        # Error messages are not exposed in HTTP responses to prevent information leakage;
        # the error type is stored instead.
        exc = ValueError("invalid value")
        result = handle_error(exc, log_exception=False)
        assert result.details.get("error_type") == "ValueError"

    def test_cause_is_linked_to_original(self):
        exc = KeyError("missing_key")
        result = handle_error(exc, log_exception=False)
        assert result.cause is exc

    def test_custom_error_code_is_applied(self):
        exc = RuntimeError("boom")
        result = handle_error(exc, default_code=ErrorCode.TIMEOUT_ERROR, log_exception=False)
        assert result.error_code == ErrorCode.TIMEOUT_ERROR

    def test_context_is_added_to_details(self):
        exc = RuntimeError("crash")
        result = handle_error(exc, context={"request_id": "req-1"}, log_exception=False)
        assert result.details.get("request_id") == "req-1"
