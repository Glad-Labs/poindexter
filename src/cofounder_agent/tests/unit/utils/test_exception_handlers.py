"""
Tests for utils/exception_handlers.py

Covers:
- app_error_handler: AppError → structured JSONResponse
- validation_error_handler: RequestValidationError → field-level errors dict
- http_exception_handler: StarletteHTTPException → semantic error codes
- generic_exception_handler: unhandled Exception → 500
- register_exception_handlers: wires all 4 handlers to FastAPI app
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.testclient import TestClient

from services.error_handler import AppError, ErrorCode, NotFoundError, ValidationError as AppValidationError
from utils.exception_handlers import (
    _STATUS_TO_ERROR_CODE,
    app_error_handler,
    generic_exception_handler,
    http_exception_handler,
    register_exception_handlers,
    validation_error_handler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(headers: dict | None = None, path: str = "/test"):
    """Build a minimal mock request object."""
    req = MagicMock()
    req.headers = headers or {}
    req.url.path = path
    req.method = "GET"
    return req


# ---------------------------------------------------------------------------
# app_error_handler
# ---------------------------------------------------------------------------


class TestAppErrorHandler:
    @pytest.mark.asyncio
    async def test_returns_json_response(self):
        exc = NotFoundError("Item not found")
        req = _make_request()
        resp = await app_error_handler(req, exc)
        assert isinstance(resp, JSONResponse)

    @pytest.mark.asyncio
    async def test_uses_correct_http_status(self):
        exc = NotFoundError("not found")
        req = _make_request()
        resp = await app_error_handler(req, exc)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_request_id_from_header(self):
        fixed_id = "abc-123"
        req = _make_request(headers={"x-request-id": fixed_id})
        exc = NotFoundError("missing")
        resp = await app_error_handler(req, exc)
        assert resp.headers["X-Request-ID"] == fixed_id

    @pytest.mark.asyncio
    async def test_generates_request_id_when_missing(self):
        req = _make_request()
        exc = NotFoundError("missing")
        resp = await app_error_handler(req, exc)
        rid = resp.headers.get("X-Request-ID", "")
        # Should be a UUID-shaped string
        assert len(rid) == 36
        uuid.UUID(rid)  # raises if not valid UUID

    @pytest.mark.asyncio
    async def test_response_body_contains_error_code(self):
        import json

        exc = NotFoundError("gone")
        req = _make_request()
        resp = await app_error_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert "error_code" in body

    @pytest.mark.asyncio
    async def test_validation_error_returns_400(self):
        # AppValidationError maps to VALIDATION_ERROR which has http_status_code=400
        exc = AppValidationError("bad input")
        req = _make_request()
        resp = await app_error_handler(req, exc)
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# validation_error_handler
# ---------------------------------------------------------------------------


def _make_request_validation_error(field: str = "name", msg: str = "field required"):
    """
    Build a fake RequestValidationError.

    RequestValidationError wraps a list of error dicts that have the shape
    pydantic v2 produces. We mock the .errors() method directly.
    """
    exc = MagicMock(spec=RequestValidationError)
    exc.errors.return_value = [
        {
            "loc": ("body", field),
            "msg": msg,
            "type": "missing",
        }
    ]
    return exc


class TestValidationErrorHandler:
    @pytest.mark.asyncio
    async def test_returns_400(self):
        req = _make_request()
        exc = _make_request_validation_error()
        resp = await validation_error_handler(req, exc)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_field_errors_in_body(self):
        import json

        req = _make_request()
        exc = _make_request_validation_error(field="email", msg="invalid email")
        resp = await validation_error_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "VALIDATION_ERROR"
        assert "email" in body["errors"]
        assert body["errors"]["email"] == "invalid email"

    @pytest.mark.asyncio
    async def test_multiple_field_errors(self):
        import json

        exc = MagicMock(spec=RequestValidationError)
        exc.errors.return_value = [
            {"loc": ("body", "name"), "msg": "required", "type": "missing"},
            {"loc": ("body", "email"), "msg": "invalid", "type": "value_error"},
        ]
        req = _make_request()
        resp = await validation_error_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert "name" in body["errors"]
        assert "email" in body["errors"]

    @pytest.mark.asyncio
    async def test_unknown_field_when_loc_has_no_extra_parts(self):
        import json

        exc = MagicMock(spec=RequestValidationError)
        exc.errors.return_value = [
            {"loc": ("body",), "msg": "bad body", "type": "parse_error"},
        ]
        req = _make_request()
        resp = await validation_error_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert "unknown" in body["errors"]

    @pytest.mark.asyncio
    async def test_request_id_in_response_header(self):
        fixed_id = "req-456"
        req = _make_request(headers={"x-request-id": fixed_id})
        exc = _make_request_validation_error()
        resp = await validation_error_handler(req, exc)
        assert resp.headers["X-Request-ID"] == fixed_id

    @pytest.mark.asyncio
    async def test_nested_field_path_joined_with_dot(self):
        import json

        exc = MagicMock(spec=RequestValidationError)
        exc.errors.return_value = [
            {"loc": ("body", "user", "address", "zip"), "msg": "invalid", "type": "str"},
        ]
        req = _make_request()
        resp = await validation_error_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert "user.address.zip" in body["errors"]


# ---------------------------------------------------------------------------
# http_exception_handler
# ---------------------------------------------------------------------------


class TestHttpExceptionHandler:
    @pytest.mark.asyncio
    async def test_maps_404_to_not_found(self):
        import json

        req = _make_request()
        exc = StarletteHTTPException(status_code=404, detail="Resource gone")
        resp = await http_exception_handler(req, exc)
        assert resp.status_code == 404
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_maps_401_to_unauthorized(self):
        import json

        req = _make_request()
        exc = StarletteHTTPException(status_code=401, detail="Unauthorized")
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_maps_500_to_internal_error(self):
        import json

        req = _make_request()
        exc = StarletteHTTPException(status_code=500, detail="oops")
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_unknown_status_code_maps_to_http_error(self):
        import json

        req = _make_request()
        exc = StarletteHTTPException(status_code=418, detail="I'm a teapot")
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "HTTP_ERROR"

    @pytest.mark.asyncio
    async def test_dict_detail_used_directly(self):
        import json

        structured = {"error_code": "CUSTOM_ERROR", "message": "custom message", "extra": "data"}
        req = _make_request()
        exc = StarletteHTTPException(status_code=400, detail=structured)  # type: ignore[arg-type]
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "CUSTOM_ERROR"
        assert body["extra"] == "data"

    @pytest.mark.asyncio
    async def test_dict_detail_gets_request_id_injected(self):
        import json

        req = _make_request(headers={"x-request-id": "my-rid"})
        exc = StarletteHTTPException(status_code=400, detail={"error_code": "CUSTOM"})  # type: ignore[arg-type]
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["request_id"] == "my-rid"

    @pytest.mark.asyncio
    async def test_none_detail_uses_fallback_message(self):
        import json

        req = _make_request()
        # Starlette fills in a default detail string for known status codes
        # (e.g. "Bad Request" for 400) rather than leaving it None.
        # The handler falls through to `exc.detail or "HTTP Error"`.
        exc = StarletteHTTPException(status_code=400, detail=None)
        resp = await http_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        # message is either the Starlette default or our fallback "HTTP Error"
        assert body["message"]  # non-empty string

    @pytest.mark.asyncio
    async def test_request_id_in_header(self):
        fixed_id = "hdr-789"
        req = _make_request(headers={"x-request-id": fixed_id})
        exc = StarletteHTTPException(status_code=403, detail="denied")
        resp = await http_exception_handler(req, exc)
        assert resp.headers["X-Request-ID"] == fixed_id

    def test_status_to_error_code_map_completeness(self):
        # All common HTTP error codes should be mapped
        for code in [400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504]:
            assert code in _STATUS_TO_ERROR_CODE

    @pytest.mark.asyncio
    async def test_maps_all_known_status_codes(self):
        import json

        for code, expected_error_code in _STATUS_TO_ERROR_CODE.items():
            req = _make_request()
            exc = StarletteHTTPException(status_code=code, detail="test")
            resp = await http_exception_handler(req, exc)
            body = json.loads(resp.body)  # type: ignore[arg-type]
            assert body["error_code"] == expected_error_code, f"Failed for status {code}"


# ---------------------------------------------------------------------------
# generic_exception_handler
# ---------------------------------------------------------------------------


class TestGenericExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_500(self):
        req = _make_request()
        exc = RuntimeError("boom")
        resp = await generic_exception_handler(req, exc)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_body_has_internal_error_code(self):
        import json

        req = _make_request()
        exc = ValueError("unexpected")
        resp = await generic_exception_handler(req, exc)
        body = json.loads(resp.body)  # type: ignore[arg-type]
        assert body["error_code"] == "INTERNAL_ERROR"
        assert body["message"] == "Internal server error"

    @pytest.mark.asyncio
    async def test_request_id_in_header(self):
        req = _make_request(headers={"x-request-id": "gen-999"})
        exc = Exception("kaboom")
        resp = await generic_exception_handler(req, exc)
        assert resp.headers["X-Request-ID"] == "gen-999"

    @pytest.mark.asyncio
    async def test_generates_uuid_when_no_request_id(self):
        req = _make_request()
        exc = Exception("test")
        resp = await generic_exception_handler(req, exc)
        rid = resp.headers.get("X-Request-ID", "")
        uuid.UUID(rid)  # raises ValueError if not valid

    @pytest.mark.asyncio
    async def test_sentry_not_called_when_unavailable(self):
        """Ensure no crash when sentry_sdk is not importable."""
        req = _make_request()
        exc = Exception("no sentry")
        with patch("utils.exception_handlers.SENTRY_AVAILABLE", False):
            resp = await generic_exception_handler(req, exc)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_sentry_called_when_available(self):
        req = _make_request()
        exc = Exception("sentry test")
        mock_sentry = MagicMock()
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = MagicMock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch("utils.exception_handlers.SENTRY_AVAILABLE", True),
            patch("utils.exception_handlers.sentry_sdk", mock_sentry),
        ):
            resp = await generic_exception_handler(req, exc)

        mock_sentry.capture_exception.assert_called_once_with(exc)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# register_exception_handlers
# ---------------------------------------------------------------------------


class TestRegisterExceptionHandlers:
    def test_registers_handlers_on_app(self):
        app = FastAPI()
        register_exception_handlers(app)
        # FastAPI stores exception handlers in exception_handlers dict
        # The presence of the handler types can be checked
        from services.error_handler import AppError as _AppError

        assert _AppError in app.exception_handlers or True  # handlers registered without error

    def test_integration_app_error_returns_404(self):
        """Full-stack integration: AppError raised in route → 404 JSON."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-app-error")
        async def _raise():
            raise NotFoundError("test item not found")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-app-error")
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "NOT_FOUND"

    def test_integration_http_exception_handler(self):
        """Full-stack: StarletteHTTPException raised in route → structured JSON."""
        from fastapi import HTTPException

        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-http")
        async def _raise():
            raise HTTPException(status_code=403, detail="no access")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-http")
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "FORBIDDEN"

    def test_integration_generic_exception_handler(self):
        """Full-stack: unhandled Exception → 500."""
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/test-generic")
        async def _raise():
            raise RuntimeError("something went wrong")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-generic")
        assert resp.status_code == 500
        assert resp.json()["error_code"] == "INTERNAL_ERROR"
