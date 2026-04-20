"""
Unit tests for the error_handler module.

Tests error classification, HTTP status mapping, error conversion, and the
handle_error() helper. All tests are pure-function — zero DB or network calls.
"""

import pytest
from fastapi import status

from services.error_handler import (
    AppError,
    AppTimeoutError,
    ConflictError,
    DatabaseError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ServiceError,
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

    def test_timeout_error_is_504(self):
        err = AppTimeoutError("llm call timed out")
        assert err.http_status_code == status.HTTP_504_GATEWAY_TIMEOUT


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

    def test_default_message_does_not_leak_exception_text(self):
        """Exposing raw exception text in HTTP responses is an information-leak risk."""
        exc = RuntimeError("internal database password=secret123 leaked")
        result = handle_error(exc, log_exception=False)
        assert "secret123" not in result.message

    def test_log_exception_false_does_not_log(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR, logger="services.error_handler"):
            handle_error(RuntimeError("silent"), log_exception=False)
        # No ERROR-level records from this module
        assert not any(
            r.name == "services.error_handler" and r.levelno >= logging.ERROR
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# create_error_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateErrorResponse:
    def test_app_error_passes_through_request_id(self):
        from services.error_handler import create_error_response

        err = ValidationError("bad field")
        response = create_error_response(err, request_id="req-42")

        assert response.request_id == "req-42"
        assert response.error_code == ErrorCode.VALIDATION_ERROR.value

    def test_generic_exception_converted_via_handle_error(self):
        from services.error_handler import create_error_response

        exc = RuntimeError("crash")
        response = create_error_response(exc, request_id="req-1")

        # handle_error converts to ServiceError, so error_code is SERVICE_ERROR
        # (the default code from handle_error()'s INTERNAL_ERROR is overridden by ServiceError class default)
        assert response.request_id == "req-1"
        assert response.error_code in (
            ErrorCode.SERVICE_ERROR.value,
            ErrorCode.INTERNAL_ERROR.value,
        )

    def test_request_id_optional(self):
        from services.error_handler import create_error_response

        err = NotFoundError("task missing", resource_type="task")
        response = create_error_response(err)

        assert response.request_id is None
        assert response.error_code == ErrorCode.NOT_FOUND.value

    def test_app_error_request_id_is_overwritten(self):
        """If the AppError already had a request_id, create_error_response should set the new one."""
        from services.error_handler import create_error_response

        err = ValidationError("bad", request_id="old-req")
        response = create_error_response(err, request_id="new-req")

        assert response.request_id == "new-req"


# ---------------------------------------------------------------------------
# ValidationError details builder
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidationErrorDetails:
    def test_field_in_details(self):
        err = ValidationError("bad field", field="email")
        assert err.details.get("field") == "email"

    def test_value_in_details_as_string(self):
        err = ValidationError("bad value", field="age", value=42)
        assert err.details.get("value") == "42"

    def test_constraint_in_details(self):
        err = ValidationError("violation", constraint="must be > 0")
        assert err.details.get("constraint") == "must be > 0"

    def test_none_values_filtered_out(self):
        """Fields explicitly None should not appear in details."""
        err = ValidationError("msg", field="email", value=None)
        # value=None should be filtered, but field should remain
        assert "value" not in err.details
        assert err.details.get("field") == "email"

    def test_no_optional_args_returns_empty_or_minimal_details(self):
        err = ValidationError("just a message")
        # All optional details were None and filtered
        assert err.details == {}

    def test_value_zero_is_kept(self):
        """Numeric zero is not None and should be stored."""
        err = ValidationError("bad", field="count", value=0)
        assert err.details.get("value") == "0"


# ---------------------------------------------------------------------------
# NotFoundError details edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotFoundErrorDetails:
    def test_no_optional_args_returns_empty_details(self):
        err = NotFoundError("missing")
        assert err.details == {}

    def test_resource_type_only(self):
        err = NotFoundError("missing", resource_type="user")
        assert err.details == {"resource_type": "user"}

    def test_resource_id_only(self):
        err = NotFoundError("missing", resource_id="abc-123")
        assert err.details == {"resource_id": "abc-123"}

    def test_resource_id_int_coerced_to_string(self):
        err = NotFoundError("missing", resource_type="user", resource_id=42)
        assert err.details.get("resource_id") == "42"


# ---------------------------------------------------------------------------
# AppError edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAppErrorEdgeCases:
    def test_str_without_details_no_pipe(self):
        err = AppError("simple message")
        assert "Details" not in str(err)
        assert "simple message" in str(err)

    def test_request_id_stored(self):
        err = AppError("msg", request_id="req-99")
        assert err.request_id == "req-99"

    def test_request_id_default_none(self):
        err = AppError("msg")
        assert err.request_id is None

    def test_to_response_with_empty_details_returns_none(self):
        err = AppError("msg")
        response = err.to_response()
        assert response.details is None

    def test_to_response_with_populated_details(self):
        err = AppError("msg", details={"k": "v"})
        response = err.to_response()
        assert response.details == {"k": "v"}

    def test_to_response_includes_request_id(self):
        err = AppError("msg", request_id="req-123")
        response = err.to_response()
        assert response.request_id == "req-123"

    def test_to_http_exception_detail_structure(self):
        err = ValidationError("bad", field="email")
        exc = err.to_http_exception()
        assert isinstance(exc.detail, dict)
        assert exc.detail["error_code"] == ErrorCode.VALIDATION_ERROR.value
        assert exc.detail["message"] == "bad"

    def test_to_http_exception_excludes_none_fields(self):
        """exclude_none=True should drop empty fields from the response payload."""
        err = AppError("msg")
        exc = err.to_http_exception()
        # request_id is None, should be excluded
        assert "request_id" not in exc.detail
        # details is empty dict → None → excluded
        assert "details" not in exc.detail

    def test_inherits_from_exception(self):
        """Should be raisable as a Python exception."""
        with pytest.raises(AppError, match="boom"):
            raise AppError("boom")

    def test_subclass_uses_class_default_error_code(self):
        """ValidationError should use VALIDATION_ERROR even without explicit code."""
        err = ValidationError("bad")
        assert err.error_code == ErrorCode.VALIDATION_ERROR

    def test_explicit_error_code_overrides_class_default(self):
        """Passing an explicit error_code should override the class default."""
        err = ValidationError("bad", error_code=ErrorCode.INVALID_INPUT)
        assert err.error_code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# ErrorCode enum coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorCodeEnum:
    def test_all_codes_are_strings(self):
        for code in ErrorCode:
            assert isinstance(code.value, str)
            assert code.value == code.value.upper()  # all UPPER_SNAKE

    def test_validation_codes_present(self):
        assert ErrorCode.VALIDATION_ERROR
        assert ErrorCode.INVALID_INPUT
        assert ErrorCode.MISSING_REQUIRED_FIELD

    def test_auth_codes_present(self):
        assert ErrorCode.UNAUTHORIZED
        assert ErrorCode.FORBIDDEN
        assert ErrorCode.PERMISSION_DENIED

    def test_not_found_codes_present(self):
        assert ErrorCode.NOT_FOUND
        assert ErrorCode.RESOURCE_NOT_FOUND
        assert ErrorCode.TASK_NOT_FOUND
        assert ErrorCode.USER_NOT_FOUND

    def test_server_codes_present(self):
        assert ErrorCode.INTERNAL_ERROR
        assert ErrorCode.DATABASE_ERROR
        assert ErrorCode.SERVICE_ERROR
        assert ErrorCode.TIMEOUT_ERROR
