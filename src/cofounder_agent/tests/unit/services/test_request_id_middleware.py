"""
Unit tests for middleware/request_id.py

Tests:
- UUID generation when no incoming header present
- Header propagation when caller provides X-Request-ID
- Response header always contains X-Request-ID
- ContextVar is reset after request completes
- RequestIDFilter adds request_id to log records
- RequestIDFilter uses '-' default outside of request context
"""

import logging
import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient

# The middleware lives in src/cofounder_agent/middleware/request_id.py
# When running under pytest from src/cofounder_agent/ the import path is:
from middleware.request_id import (
    HEADER_NAME,
    RequestIDFilter,
    RequestIDMiddleware,
    _request_id_var,
    get_request_id,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app with the RequestIDMiddleware."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/ping")
    async def ping():
        # Return the request ID visible from within the request context
        return {"request_id": get_request_id()}

    return app


# ============================================================================
# Tests
# ============================================================================


class TestRequestIDMiddleware:
    """Tests for RequestIDMiddleware."""

    def setup_method(self):
        self.app = _make_app()
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def test_generates_uuid_when_no_header(self):
        """A fresh UUIDv4 is generated when the client sends no X-Request-ID."""
        response = self.client.get("/ping")
        assert response.status_code == 200
        rid = response.headers.get(HEADER_NAME)
        assert rid is not None
        # Should be a valid UUID
        parsed = uuid.UUID(rid)  # raises if invalid
        assert str(parsed) == rid

    def test_propagates_caller_request_id(self):
        """Caller-supplied X-Request-ID is used as-is (no new UUID generated)."""
        caller_id = "my-trace-abc-123"
        response = self.client.get("/ping", headers={HEADER_NAME: caller_id})
        assert response.status_code == 200
        assert response.headers.get(HEADER_NAME) == caller_id

    def test_response_always_contains_header(self):
        """X-Request-ID is always present in the response."""
        response = self.client.get("/ping")
        assert HEADER_NAME in response.headers

    def test_request_id_visible_in_handler(self):
        """get_request_id() returns the correct ID inside the request context."""
        caller_id = "visible-in-handler-456"
        response = self.client.get("/ping", headers={HEADER_NAME: caller_id})
        body = response.json()
        assert body["request_id"] == caller_id

    def test_context_var_reset_after_request(self):
        """ContextVar is reset to None after the request completes."""
        # Before any request: should be None
        assert _request_id_var.get() is None

        self.client.get("/ping")

        # After request completes: should be reset to None
        assert _request_id_var.get() is None

    def test_different_requests_get_different_ids(self):
        """Two consecutive requests without a caller ID get different UUIDs."""
        r1 = self.client.get("/ping")
        r2 = self.client.get("/ping")
        id1 = r1.headers.get(HEADER_NAME)
        id2 = r2.headers.get(HEADER_NAME)
        assert id1 != id2


class TestRequestIDFilter:
    """Tests for RequestIDFilter logging filter."""

    def test_adds_request_id_to_record(self):
        """Filter injects request_id into the log record."""
        # Set a known request ID in the ContextVar
        token = _request_id_var.set("filter-test-id")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="test",
                args=(),
                exc_info=None,
            )
            f = RequestIDFilter()
            result = f.filter(record)
            assert result is True
            assert record.request_id == "filter-test-id"  # type: ignore[attr-defined]
        finally:
            _request_id_var.reset(token)

    def test_defaults_to_dash_outside_request(self):
        """Filter uses '-' when no request ID is in context."""
        # Ensure ContextVar is not set
        assert _request_id_var.get() is None
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        f = RequestIDFilter()
        f.filter(record)
        assert record.request_id == "-"  # type: ignore[attr-defined]

    def test_filter_always_returns_true(self):
        """Filter never suppresses log records."""
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="msg",
            args=(),
            exc_info=None,
        )
        f = RequestIDFilter()
        assert f.filter(record) is True


class TestGetRequestID:
    """Tests for get_request_id() helper function."""

    def test_returns_none_outside_request(self):
        """Returns None when called outside a request context."""
        assert get_request_id() is None

    def test_returns_current_id_within_context(self):
        """Returns the correct ID when ContextVar is set."""
        token = _request_id_var.set("test-get-id")
        try:
            assert get_request_id() == "test-get-id"
        finally:
            _request_id_var.reset(token)
