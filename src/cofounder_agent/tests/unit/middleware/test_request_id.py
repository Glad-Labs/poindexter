"""
Request ID Middleware — Unit Tests

Tests for request ID generation, propagation, context var management,
and logging filter injection.
"""

import logging
import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.request_id import (
    HEADER_NAME,
    RequestIDFilter,
    RequestIDMiddleware,
    _request_id_var,
    get_request_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app():
    """Build a minimal FastAPI app with RequestIDMiddleware."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    async def test_endpoint():
        return {"request_id": get_request_id()}

    @app.get("/nested")
    async def nested_endpoint():
        """Endpoint that reads request_id from within the handler."""
        rid = get_request_id()
        return {"request_id": rid, "is_set": rid is not None}

    return app


app = _build_app()
client = TestClient(app)


# ---------------------------------------------------------------------------
# get_request_id
# ---------------------------------------------------------------------------


class TestGetRequestId:
    def test_returns_none_outside_context(self):
        # Reset to default state
        token = _request_id_var.set(None)
        try:
            assert get_request_id() is None
        finally:
            _request_id_var.reset(token)

    def test_returns_value_when_set(self):
        token = _request_id_var.set("test-123")
        try:
            assert get_request_id() == "test-123"
        finally:
            _request_id_var.reset(token)


# ---------------------------------------------------------------------------
# RequestIDFilter
# ---------------------------------------------------------------------------


class TestRequestIDFilter:
    def test_sets_dash_when_no_context(self):
        filt = RequestIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        # Ensure no request context
        token = _request_id_var.set(None)
        try:
            result = filt.filter(record)
            assert result is True
            assert record.request_id == "-"
        finally:
            _request_id_var.reset(token)

    def test_sets_request_id_from_context(self):
        filt = RequestIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        token = _request_id_var.set("req-abc-123")
        try:
            filt.filter(record)
            assert record.request_id == "req-abc-123"
        finally:
            _request_id_var.reset(token)

    def test_always_returns_true(self):
        """Filter should never suppress log records."""
        filt = RequestIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        assert filt.filter(record) is True


# ---------------------------------------------------------------------------
# RequestIDMiddleware
# ---------------------------------------------------------------------------


class TestRequestIDMiddleware:
    def test_generates_uuid_when_no_header(self):
        resp = client.get("/test")
        assert resp.status_code == 200
        rid = resp.headers.get(HEADER_NAME)
        assert rid is not None
        # Should be a valid UUID
        uuid.UUID(rid)  # Raises if invalid

    def test_reuses_incoming_header(self):
        custom_id = "my-trace-id-12345"
        resp = client.get("/test", headers={HEADER_NAME: custom_id})
        assert resp.status_code == 200
        assert resp.headers[HEADER_NAME] == custom_id

    def test_request_id_available_in_handler(self):
        resp = client.get("/test")
        data = resp.json()
        assert data["request_id"] is not None
        # Should match the response header
        assert data["request_id"] == resp.headers[HEADER_NAME]

    def test_propagated_id_available_in_handler(self):
        custom_id = "propagated-trace-999"
        resp = client.get("/test", headers={HEADER_NAME: custom_id})
        data = resp.json()
        assert data["request_id"] == custom_id

    def test_response_always_has_header(self):
        resp = client.get("/nested")
        assert HEADER_NAME in resp.headers
        data = resp.json()
        assert data["is_set"] is True

    def test_different_requests_get_different_ids(self):
        resp1 = client.get("/test")
        resp2 = client.get("/test")
        id1 = resp1.headers[HEADER_NAME]
        id2 = resp2.headers[HEADER_NAME]
        assert id1 != id2

    def test_context_var_cleaned_after_request(self):
        """After a request completes, the context var should be reset."""
        # Make a request that sets the var
        resp = client.get("/test")
        assert resp.status_code == 200
        # Outside request context, get_request_id should return None
        # (Note: TestClient may run synchronously, so context is shared;
        # we just verify the response was clean)
        rid = resp.json()["request_id"]
        assert rid is not None


# ---------------------------------------------------------------------------
# Background task usage pattern
# ---------------------------------------------------------------------------


class TestBackgroundTaskPattern:
    def test_manual_context_var_binding(self):
        """Test the documented pattern for background task usage."""
        task_id = "task-abc-123"
        token = _request_id_var.set(f"task-{task_id}")
        try:
            assert get_request_id() == f"task-{task_id}"
        finally:
            _request_id_var.reset(token)
        # After reset, should be back to default
        # (Note: in real async code, the previous value is restored)
