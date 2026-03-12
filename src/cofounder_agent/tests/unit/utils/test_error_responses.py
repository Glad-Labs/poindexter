"""
Unit tests for utils.error_responses module.

All tests are pure — zero DB, LLM, or network calls.
Covers ErrorResponseBuilder fluent API, factory methods, and Pydantic models.
"""

import pytest

from utils.error_responses import (
    ErrorDetail,
    ErrorResponse,
    ErrorResponseBuilder,
    SuccessResponse,
)


# ---------------------------------------------------------------------------
# ErrorDetail model
# ---------------------------------------------------------------------------


class TestErrorDetail:
    def test_message_is_required(self):
        detail = ErrorDetail(message="Something wrong")  # type: ignore[call-arg]
        assert detail.message == "Something wrong"

    def test_field_is_optional(self):
        detail = ErrorDetail(message="Required")  # type: ignore[call-arg]
        assert detail.field is None

    def test_code_is_optional(self):
        detail = ErrorDetail(message="Error")  # type: ignore[call-arg]
        assert detail.code is None

    def test_all_fields(self):
        detail = ErrorDetail(field="email", message="Invalid email", code="INVALID_FORMAT")
        assert detail.field == "email"
        assert detail.message == "Invalid email"
        assert detail.code == "INVALID_FORMAT"


# ---------------------------------------------------------------------------
# ErrorResponse model
# ---------------------------------------------------------------------------


class TestErrorResponseModel:
    def test_status_defaults_to_error(self):
        resp = ErrorResponse(  # type: ignore[call-arg]
            error_code="SOME_ERROR",
            message="An error occurred",
        )
        assert resp.status == "error"

    def test_required_fields(self):
        resp = ErrorResponse(error_code="NOT_FOUND", message="Resource not found")  # type: ignore[call-arg]
        assert resp.error_code == "NOT_FOUND"
        assert resp.message == "Resource not found"

    def test_optional_fields_default_to_none(self):
        resp = ErrorResponse(error_code="ERR", message="msg")  # type: ignore[call-arg]
        assert resp.details is None
        assert resp.request_id is None
        assert resp.path is None
        assert resp.timestamp is None


# ---------------------------------------------------------------------------
# SuccessResponse model
# ---------------------------------------------------------------------------


class TestSuccessResponseModel:
    def test_status_defaults_to_success(self):
        resp = SuccessResponse(data={"id": 1})  # type: ignore[call-arg]
        assert resp.status == "success"

    def test_data_field(self):
        resp = SuccessResponse(data=[1, 2, 3])  # type: ignore[call-arg]
        assert resp.data == [1, 2, 3]

    def test_optional_fields_default_to_none(self):
        resp = SuccessResponse(data="ok")  # type: ignore[call-arg]
        assert resp.request_id is None
        assert resp.timestamp is None


# ---------------------------------------------------------------------------
# ErrorResponseBuilder — fluent API
# ---------------------------------------------------------------------------


class TestErrorResponseBuilderFluent:
    def test_build_requires_error_code(self):
        builder = ErrorResponseBuilder()
        builder.message("Something failed")
        with pytest.raises(ValueError, match="error_code is required"):
            builder.build()

    def test_build_requires_message(self):
        builder = ErrorResponseBuilder()
        builder.error_code("ERR")
        with pytest.raises(ValueError, match="message is required"):
            builder.build()

    def test_basic_build(self):
        response = (
            ErrorResponseBuilder()
            .error_code("MY_ERROR")
            .message("Something went wrong")
            .build()
        )
        assert response.error_code == "MY_ERROR"
        assert response.message == "Something went wrong"

    def test_with_field_error(self):
        response = (
            ErrorResponseBuilder()
            .error_code("VALIDATION_ERROR")
            .message("Validation failed")
            .with_field_error("task_name", "Field required", "REQUIRED")
            .build()
        )
        assert response.details is not None
        assert len(response.details) == 1
        assert response.details[0].field == "task_name"

    def test_with_detail_no_field(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .with_detail("General error")
            .build()
        )
        assert response.details is not None
        assert response.details[0].field is None
        assert response.details[0].message == "General error"

    def test_with_details_list(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .with_details([
                {"field": "name", "message": "Required", "code": "REQ"},
                {"field": "email", "message": "Invalid"},
            ])
            .build()
        )
        assert response.details is not None
        assert len(response.details) == 2

    def test_request_id(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .request_id("req-abc123")
            .build()
        )
        assert response.request_id == "req-abc123"

    def test_path(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .path("/api/tasks")
            .build()
        )
        assert response.path == "/api/tasks"

    def test_timestamp_adds_iso_string(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .timestamp()
            .build()
        )
        assert response.timestamp is not None
        assert "Z" in response.timestamp

    def test_build_dict_returns_dict(self):
        result = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .build_dict()
        )
        assert isinstance(result, dict)
        assert result["error_code"] == "ERR"

    def test_build_dict_excludes_none(self):
        result = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .build_dict()
        )
        assert "request_id" not in result
        assert "path" not in result

    def test_method_chaining_returns_builder(self):
        builder = ErrorResponseBuilder()
        result = builder.error_code("E").message("m").request_id("r").path("/p")
        assert isinstance(result, ErrorResponseBuilder)

    def test_no_details_when_none_added(self):
        response = (
            ErrorResponseBuilder()
            .error_code("ERR")
            .message("msg")
            .build()
        )
        assert response.details is None


# ---------------------------------------------------------------------------
# ErrorResponseBuilder — factory methods
# ---------------------------------------------------------------------------


class TestErrorResponseBuilderFactories:
    def test_validation_error_factory(self):
        builder = ErrorResponseBuilder.validation_error("Validation failed")
        response = builder.build()
        assert response.error_code == "VALIDATION_ERROR"
        assert "Validation" in response.message

    def test_validation_error_with_details(self):
        builder = ErrorResponseBuilder.validation_error(
            details=[("task_name", "Required"), ("topic", "Too short")]
        )
        response = builder.build()
        assert response.details is not None
        assert len(response.details) == 2
        fields = [d.field for d in response.details]
        assert "task_name" in fields
        assert "topic" in fields

    def test_not_found_with_resource_id(self):
        builder = ErrorResponseBuilder.not_found("task", resource_id="task-123")
        response = builder.build()
        assert response.error_code == "NOT_FOUND"
        assert "task-123" in response.message

    def test_not_found_without_resource_id(self):
        builder = ErrorResponseBuilder.not_found("user")
        response = builder.build()
        assert response.error_code == "NOT_FOUND"
        assert "User" in response.message

    def test_not_found_with_custom_message(self):
        builder = ErrorResponseBuilder.not_found("task", message="Custom message")
        response = builder.build()
        assert response.message == "Custom message"

    def test_unauthorized_factory(self):
        response = ErrorResponseBuilder.unauthorized().build()
        assert response.error_code == "UNAUTHORIZED"

    def test_unauthorized_custom_message(self):
        response = ErrorResponseBuilder.unauthorized("Please log in").build()
        assert response.message == "Please log in"

    def test_forbidden_factory(self):
        response = ErrorResponseBuilder.forbidden().build()
        assert response.error_code == "FORBIDDEN"

    def test_conflict_factory(self):
        response = ErrorResponseBuilder.conflict("task").build()
        assert response.error_code == "CONFLICT"
        assert "Task" in response.message

    def test_conflict_custom_message(self):
        response = ErrorResponseBuilder.conflict("user", message="Email already taken").build()
        assert response.message == "Email already taken"

    def test_server_error_factory(self):
        response = ErrorResponseBuilder.server_error().build()
        assert response.error_code == "INTERNAL_ERROR"

    def test_unprocessable_factory(self):
        response = ErrorResponseBuilder.unprocessable().build()
        assert response.error_code == "UNPROCESSABLE_ENTITY"

    def test_unprocessable_with_details(self):
        response = ErrorResponseBuilder.unprocessable(
            details=[("field", "error message")]
        ).build()
        assert response.details is not None
        assert len(response.details) == 1

    def test_rate_limited_factory(self):
        response = ErrorResponseBuilder.rate_limited().build()
        assert response.error_code == "RATE_LIMIT_EXCEEDED"

    def test_service_unavailable_factory(self):
        response = ErrorResponseBuilder.service_unavailable().build()
        assert response.error_code == "SERVICE_UNAVAILABLE"

    def test_factory_returns_builder_for_chaining(self):
        """Factory methods return a builder, not a response — chain is possible."""
        response = (
            ErrorResponseBuilder.not_found("task", resource_id="123")
            .request_id("req-xyz")
            .path("/api/tasks/123")
            .build()
        )
        assert response.request_id == "req-xyz"
        assert response.path == "/api/tasks/123"
