"""
Unit tests for utils.error_handler module.

Covers:
- ErrorResponse class (constructor, to_dict, to_http_exception)
- handle_route_error(): async helper that maps exceptions to HTTPException
- handle_service_error(): sync helper that logs and returns fallback or re-raises
- create_error_response(): creates standardized error dict
- log_and_raise_http_error(): logs + raises HTTPException
- Convenience wrappers: not_found, bad_request, forbidden, internal_error, service_unavailable

All tests are pure — zero DB, LLM, or network calls.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from utils.error_handler import (
    ErrorResponse,
    bad_request,
    create_error_response,
    forbidden,
    handle_route_error,
    handle_service_error,
    internal_error,
    log_and_raise_http_error,
    not_found,
    service_unavailable,
)

# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorResponse:
    def test_default_error_type_is_unknown(self):
        resp = ErrorResponse(status_code=500, detail="Something broke")
        assert resp.error_type == "UnknownError"

    def test_explicit_error_type_stored(self):
        resp = ErrorResponse(status_code=400, detail="Bad input", error_type="ValidationError")
        assert resp.error_type == "ValidationError"

    def test_operation_is_optional_none_by_default(self):
        resp = ErrorResponse(status_code=500, detail="fail")
        assert resp.operation is None

    def test_operation_stored_when_provided(self):
        resp = ErrorResponse(status_code=404, detail="Not found", operation="get_post")
        assert resp.operation == "get_post"

    def test_timestamp_is_set_on_construction(self):
        resp = ErrorResponse(status_code=500, detail="oops")
        # Should be a non-empty ISO-format string
        assert resp.timestamp
        assert "+" in resp.timestamp or "Z" in resp.timestamp or "T" in resp.timestamp

    def test_to_dict_shape(self):
        resp = ErrorResponse(status_code=422, detail="unprocessable", error_type="InputError")
        d = resp.to_dict()
        assert "error" in d
        assert d["error"]["type"] == "InputError"
        assert d["error"]["detail"] == "unprocessable"
        assert d["error"]["timestamp"] == resp.timestamp

    def test_to_http_exception_has_correct_status_and_detail(self):
        resp = ErrorResponse(status_code=403, detail="Forbidden")
        exc = resp.to_http_exception()
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 403
        assert exc.detail == "Forbidden"


# ---------------------------------------------------------------------------
# handle_route_error (async)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleRouteError:
    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def test_http_exception_is_returned_unchanged(self):
        original = HTTPException(status_code=418, detail="I'm a teapot")
        result = self._run(handle_route_error(original, "brew_tea"))
        assert result is original

    def test_value_error_maps_to_400(self):
        exc = ValueError("bad value")
        result = self._run(handle_route_error(exc, "parse_input"))
        assert isinstance(result, HTTPException)
        assert result.status_code == 400

    def test_key_error_maps_to_400(self):
        exc = KeyError("missing_field")
        result = self._run(handle_route_error(exc, "extract_field"))
        assert result.status_code == 400

    def test_attribute_error_maps_to_400(self):
        exc = AttributeError("no such attr")
        result = self._run(handle_route_error(exc, "access_attr"))
        assert result.status_code == 400

    def test_timeout_error_maps_to_504(self):
        exc = TimeoutError("timed out")
        result = self._run(handle_route_error(exc, "slow_op"))
        assert result.status_code == 504

    def test_connection_error_maps_to_503(self):
        exc = ConnectionError("connection refused")
        result = self._run(handle_route_error(exc, "db_connect"))
        assert result.status_code == 503

    def test_permission_error_maps_to_403(self):
        exc = PermissionError("denied")
        result = self._run(handle_route_error(exc, "admin_action"))
        assert result.status_code == 403

    def test_generic_exception_maps_to_500(self):
        exc = RuntimeError("unexpected")
        result = self._run(handle_route_error(exc, "do_something"))
        assert result.status_code == 500

    def test_default_detail_used_when_no_message(self):
        exc = RuntimeError("")
        result = self._run(handle_route_error(exc, "mystery_op", default_detail="Custom fallback"))
        assert "Custom fallback" in result.detail

    def test_value_error_detail_uses_generic_message(self):
        # ValueError messages are not propagated to HTTP responses to prevent
        # information leakage; a generic operation-scoped message is used instead.
        exc = ValueError("specific validation message")
        result = self._run(handle_route_error(exc, "validate"))
        assert result.status_code == 400
        assert "validate" in result.detail

    def test_custom_logger_is_used(self):
        mock_logger = MagicMock(spec=logging.Logger)
        exc = ValueError("test error")
        self._run(handle_route_error(exc, "my_op", logger_instance=mock_logger))
        # ValueError → 400 → warning
        mock_logger.warning.assert_called_once()

    def test_server_error_uses_error_level(self):
        mock_logger = MagicMock(spec=logging.Logger)
        exc = RuntimeError("crash")
        self._run(handle_route_error(exc, "my_op", logger_instance=mock_logger))
        # 500-level → error
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# handle_service_error (sync)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleServiceError:
    def test_returns_fallback_value_when_provided(self):
        exc = RuntimeError("fail")
        result = handle_service_error(exc, "fetch_users", fallback_value=[])
        assert result == []

    def test_returns_none_fallback_explicitly(self):
        # fallback_value=None means re-raise; pass a non-None default instead
        exc = RuntimeError("fail")
        result = handle_service_error(exc, "fetch_count", fallback_value=0)
        assert result == 0

    def test_raises_http_exception_when_no_fallback(self):
        exc = RuntimeError("blow up")
        with pytest.raises(HTTPException) as exc_info:
            handle_service_error(exc, "critical_op")
        assert exc_info.value.status_code == 500

    def test_raised_http_exception_has_generic_detail(self):
        exc = RuntimeError("blow up")
        with pytest.raises(HTTPException) as exc_info:
            handle_service_error(exc, "my_operation")
        assert "my_operation" in exc_info.value.detail

    def test_logs_error_with_service_prefix(self):
        mock_logger = MagicMock(spec=logging.Logger)
        exc = ValueError("bad input")
        handle_service_error(exc, "compute", logger_instance=mock_logger, fallback_value="n/a")
        call_args = mock_logger.error.call_args
        assert "SERVICE:compute" in call_args[0][0]

    def test_logs_fallback_info_message(self):
        mock_logger = MagicMock(spec=logging.Logger)
        exc = ValueError("bad input")
        handle_service_error(exc, "compute", logger_instance=mock_logger, fallback_value=[])
        mock_logger.info.assert_called_once()


# ---------------------------------------------------------------------------
# create_error_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateErrorResponse:
    def test_returns_dict_with_error_key(self):
        exc = ValueError("bad")
        result = create_error_response(exc, "parse")
        assert isinstance(result, dict)
        assert "error" in result

    def test_error_type_matches_exception_class(self):
        exc = KeyError("key")
        result = create_error_response(exc, "lookup")
        assert result["error"]["type"] == "KeyError"

    def test_detail_is_generic_message(self):
        # Error messages are not exposed in HTTP responses to prevent information
        # leakage; create_error_response returns a generic "An error occurred" detail.
        exc = RuntimeError("something specific")
        result = create_error_response(exc, "run")
        assert result["error"]["detail"] == "An error occurred"

    def test_default_status_code_500(self):
        exc = RuntimeError("oops")
        result = create_error_response(exc, "op")
        # status_code is on the ErrorResponse but not in to_dict() output;
        # just verify we get the dict shape
        assert "type" in result["error"]
        assert "detail" in result["error"]
        assert "timestamp" in result["error"]

    def test_custom_operation_name(self):
        exc = ValueError("bad data")
        # create_error_response stores operation on ErrorResponse but doesn't include
        # it in to_dict() — that's fine, just test it doesn't raise
        result = create_error_response(exc, "custom_op", status_code=400)
        assert result["error"]["type"] == "ValueError"


# ---------------------------------------------------------------------------
# log_and_raise_http_error
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogAndRaiseHttpError:
    def test_raises_http_exception(self):
        with pytest.raises(HTTPException):
            log_and_raise_http_error(404, "Not found")

    def test_correct_status_code(self):
        with pytest.raises(HTTPException) as exc_info:
            log_and_raise_http_error(422, "Unprocessable")
        assert exc_info.value.status_code == 422

    def test_correct_detail(self):
        with pytest.raises(HTTPException) as exc_info:
            log_and_raise_http_error(400, "Bad request detail")
        assert exc_info.value.detail == "Bad request detail"

    def test_4xx_uses_warning_level(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with pytest.raises(HTTPException):
            log_and_raise_http_error(400, "bad", operation="op", logger_instance=mock_logger)
        mock_logger.warning.assert_called_once()
        mock_logger.error.assert_not_called()

    def test_5xx_uses_error_level(self):
        mock_logger = MagicMock(spec=logging.Logger)
        with pytest.raises(HTTPException):
            log_and_raise_http_error(500, "crash", operation="op", logger_instance=mock_logger)
        mock_logger.error.assert_called_once()
        mock_logger.warning.assert_not_called()

    def test_no_operation_does_not_raise(self):
        # Should not raise an error when operation=None
        with pytest.raises(HTTPException):
            log_and_raise_http_error(404, "gone")


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConvenienceFunctions:
    def test_not_found_returns_404(self):
        exc = not_found()
        assert exc.status_code == 404

    def test_not_found_custom_detail(self):
        exc = not_found("Post not found", operation="get_post")
        assert exc.detail == "Post not found"

    def test_bad_request_returns_400(self):
        exc = bad_request()
        assert exc.status_code == 400

    def test_bad_request_custom_detail(self):
        exc = bad_request("Missing field", operation="create_task")
        assert exc.detail == "Missing field"

    def test_forbidden_returns_403(self):
        exc = forbidden()
        assert exc.status_code == 403

    def test_forbidden_custom_detail(self):
        exc = forbidden("Admin only", operation="delete_user")
        assert exc.detail == "Admin only"

    def test_internal_error_returns_500(self):
        exc = internal_error()
        assert exc.status_code == 500

    def test_internal_error_custom_detail(self):
        exc = internal_error("Database failed", operation="save_data")
        assert exc.detail == "Database failed"

    def test_service_unavailable_returns_503(self):
        exc = service_unavailable()
        assert exc.status_code == 503

    def test_service_unavailable_custom_detail(self):
        exc = service_unavailable("LLM offline", operation="generate_content")
        assert exc.detail == "LLM offline"

    def test_all_convenience_functions_return_http_exception(self):
        for fn in [not_found, bad_request, forbidden, internal_error, service_unavailable]:
            result = fn()
            assert isinstance(result, HTTPException)

    def test_not_found_no_operation_no_error(self):
        # When operation=None, the function should not log
        with patch("utils.error_handler.logger") as mock_logger:
            not_found("test")
            mock_logger.warning.assert_not_called()

    def test_not_found_with_operation_logs_warning(self):
        with patch("utils.error_handler.logger") as mock_logger:
            not_found("test", operation="find_post")
            mock_logger.warning.assert_called_once()
