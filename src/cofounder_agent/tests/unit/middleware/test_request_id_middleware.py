"""
Unit tests for middleware/request_id.py

Covers:
- get_request_id() — returns None outside request context, returns value within
- RequestIDFilter — injects request_id field into log records
- RequestIDMiddleware.dispatch — generates/propagates IDs and adds response header
"""

import logging
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from middleware.request_id import (
    RequestIDFilter,
    RequestIDMiddleware,
    _request_id_var,
    get_request_id,
    HEADER_NAME,
)


# ---------------------------------------------------------------------------
# get_request_id() — module-level helper
# ---------------------------------------------------------------------------


class TestGetRequestId:
    def test_returns_none_outside_request_context(self):
        # Ensure no ID is set in this ambient context
        token = _request_id_var.set(None)
        try:
            assert get_request_id() is None
        finally:
            _request_id_var.reset(token)

    def test_returns_id_when_set_in_context(self):
        test_id = "test-request-id-abc"
        token = _request_id_var.set(test_id)
        try:
            assert get_request_id() == test_id
        finally:
            _request_id_var.reset(token)

    def test_returns_uuid_string_when_set(self):
        uid = str(uuid.uuid4())
        token = _request_id_var.set(uid)
        try:
            result = get_request_id()
            assert result == uid
        finally:
            _request_id_var.reset(token)


# ---------------------------------------------------------------------------
# RequestIDFilter — logging filter
# ---------------------------------------------------------------------------


class TestRequestIDFilter:
    def test_filter_injects_request_id_into_record(self):
        test_id = "log-filter-test-id"
        token = _request_id_var.set(test_id)
        try:
            f = RequestIDFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="",
                lineno=0, msg="msg", args=(), exc_info=None,
            )
            result = f.filter(record)
            assert result is True
            assert record.request_id == test_id  # type: ignore[attr-defined]
        finally:
            _request_id_var.reset(token)

    def test_filter_uses_dash_when_no_request_active(self):
        token = _request_id_var.set(None)
        try:
            f = RequestIDFilter()
            record = logging.LogRecord(
                name="test", level=logging.DEBUG, pathname="",
                lineno=0, msg="msg", args=(), exc_info=None,
            )
            f.filter(record)
            assert record.request_id == "-"  # type: ignore[attr-defined]
        finally:
            _request_id_var.reset(token)

    def test_filter_always_returns_true(self):
        """Filter must never suppress log records."""
        f = RequestIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="",
            lineno=0, msg="error", args=(), exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_updates_request_id_on_each_call(self):
        f = RequestIDFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="",
            lineno=0, msg="", args=(), exc_info=None,
        )

        id_one = "first-id"
        token = _request_id_var.set(id_one)
        f.filter(record)
        assert record.request_id == id_one  # type: ignore[attr-defined]
        _request_id_var.reset(token)

        id_two = "second-id"
        token = _request_id_var.set(id_two)
        f.filter(record)
        assert record.request_id == id_two  # type: ignore[attr-defined]
        _request_id_var.reset(token)


# ---------------------------------------------------------------------------
# RequestIDMiddleware.dispatch — ASGI dispatch
# ---------------------------------------------------------------------------


def _make_request(incoming_id=None):
    req = MagicMock()
    headers = {}
    if incoming_id is not None:
        headers[HEADER_NAME] = incoming_id
    req.headers = headers
    return req


def _make_response():
    resp = MagicMock()
    resp.headers = {}
    return resp


class TestRequestIDMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_generates_uuid_when_no_incoming_header(self):
        app = MagicMock()
        mw = RequestIDMiddleware(app)
        request = _make_request()
        response = _make_response()

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        assert HEADER_NAME in result.headers
        generated_id = result.headers[HEADER_NAME]
        # Must be a valid UUID v4 string
        parsed = uuid.UUID(generated_id, version=4)
        assert str(parsed) == generated_id

    @pytest.mark.asyncio
    async def test_propagates_incoming_request_id(self):
        app = MagicMock()
        mw = RequestIDMiddleware(app)
        incoming_id = "upstream-trace-id-xyz"
        request = _make_request(incoming_id=incoming_id)
        response = _make_response()

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        assert result.headers[HEADER_NAME] == incoming_id

    @pytest.mark.asyncio
    async def test_context_var_is_set_during_request(self):
        """The request ID must be accessible via get_request_id() inside call_next."""
        app = MagicMock()
        mw = RequestIDMiddleware(app)
        known_id = "known-id-during-dispatch"
        request = _make_request(incoming_id=known_id)
        response = _make_response()
        captured_id = []

        async def call_next(req):
            captured_id.append(get_request_id())
            return response

        await mw.dispatch(request, call_next)
        assert captured_id == [known_id]

    @pytest.mark.asyncio
    async def test_context_var_is_reset_after_request(self):
        """The ContextVar must be reset to its prior value once dispatch completes."""
        app = MagicMock()
        mw = RequestIDMiddleware(app)

        # Set an outer value to verify it is restored after dispatch
        outer_id = "outer-context-id"
        outer_token = _request_id_var.set(outer_id)
        try:
            request = _make_request(incoming_id="inner-id")
            response = _make_response()

            async def call_next(req):
                return response

            await mw.dispatch(request, call_next)
            # After dispatch, the ContextVar should be reset to the outer value
            assert _request_id_var.get() == outer_id
        finally:
            _request_id_var.reset(outer_token)

    @pytest.mark.asyncio
    async def test_context_var_reset_even_if_call_next_raises(self):
        """The ContextVar reset must happen even when the handler raises."""
        app = MagicMock()
        mw = RequestIDMiddleware(app)
        request = _make_request()

        async def call_next(req):
            raise RuntimeError("downstream failure")

        with pytest.raises(RuntimeError):
            await mw.dispatch(request, call_next)

        # After the error, the ContextVar should be None (reset to default)
        assert get_request_id() is None

    @pytest.mark.asyncio
    async def test_response_header_set_regardless_of_incoming(self):
        """X-Request-ID response header must always be present."""
        app = MagicMock()
        mw = RequestIDMiddleware(app)

        for incoming in (None, "some-id"):
            request = _make_request(incoming_id=incoming)
            response = _make_response()

            async def call_next(req):
                return response

            result = await mw.dispatch(request, call_next)
            assert HEADER_NAME in result.headers

    @pytest.mark.asyncio
    async def test_different_requests_get_different_generated_ids(self):
        app = MagicMock()
        mw = RequestIDMiddleware(app)
        ids = []

        for _ in range(5):
            request = _make_request()  # no incoming ID → generate fresh one
            response = _make_response()

            async def call_next(req):
                return response

            result = await mw.dispatch(request, call_next)
            ids.append(result.headers[HEADER_NAME])

        # All generated IDs must be unique
        assert len(set(ids)) == 5
