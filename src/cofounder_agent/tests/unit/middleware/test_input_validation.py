"""
Unit tests for middleware/input_validation.py — InputValidationMiddleware
"""

from unittest.mock import MagicMock

import pytest

from middleware.input_validation import InputValidationMiddleware

# ---------------------------------------------------------------------------
# Helpers — build mock starlette Request objects
# ---------------------------------------------------------------------------


def _make_request(
    path="/api/tasks",
    method="POST",
    headers=None,
    query="",
):
    """Return a minimal mock Request."""
    req = MagicMock()
    req.url.path = path
    req.url.query = query
    req.url.__str__ = MagicMock(
        return_value=f"http://localhost{path}?{query}" if query else f"http://localhost{path}"
    )
    req.method = method
    _headers = {
        "content-type": "application/json",
    }
    if headers:
        _headers.update(headers)
    req.headers = _headers
    return req


def _make_middleware():
    app = MagicMock()
    return InputValidationMiddleware(app)


# ---------------------------------------------------------------------------
# WebSocket and skip-path bypass
# ---------------------------------------------------------------------------


class TestBypassPaths:
    @pytest.mark.asyncio
    async def test_websocket_upgrade_bypasses_validation(self):
        mw = _make_middleware()
        req = _make_request(headers={"upgrade": "websocket"})

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert called

    @pytest.mark.asyncio
    async def test_health_path_bypasses_validation(self):
        mw = _make_middleware()
        req = _make_request(path="/api/health", method="GET")

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert called

    @pytest.mark.asyncio
    async def test_docs_path_bypasses_validation(self):
        mw = _make_middleware()
        req = _make_request(path="/docs", method="GET")

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        await mw.dispatch(req, call_next)
        assert called


# ---------------------------------------------------------------------------
# _validate_headers() — content-type and suspicious headers
# ---------------------------------------------------------------------------


class TestValidateHeaders:
    def test_valid_json_content_type_passes(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-type": "application/json"})
        # Should not raise
        mw._validate_headers(req)

    def test_valid_form_content_type_passes(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-type": "application/x-www-form-urlencoded"})
        mw._validate_headers(req)

    def test_multipart_content_type_passes(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-type": "multipart/form-data; boundary=abc"})
        mw._validate_headers(req)

    def test_invalid_content_type_raises_value_error(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-type": "text/xml"})
        with pytest.raises(ValueError, match="Invalid Content-Type"):
            mw._validate_headers(req)

    def test_empty_content_type_passes(self):
        """Empty content-type is not a valid MIME type, so it passes silently."""
        mw = _make_middleware()
        req = _make_request(headers={"content-type": ""})
        # Should not raise — empty mime_type is falsy so the check is skipped
        mw._validate_headers(req)

    def test_suspicious_header_too_long_raises(self):
        mw = _make_middleware()
        req = _make_request(
            headers={
                "content-type": "application/json",
                "x-forwarded-for": "x" * 1001,
            }
        )
        with pytest.raises(ValueError, match="too long"):
            mw._validate_headers(req)

    def test_suspicious_header_acceptable_length_passes(self):
        mw = _make_middleware()
        req = _make_request(
            headers={
                "content-type": "application/json",
                "x-forwarded-for": "192.168.1.1",
            }
        )
        mw._validate_headers(req)

    def test_get_request_skips_content_type_check(self):
        """GET requests to /api/ paths are not checked for Content-Type."""
        mw = _make_middleware()
        req = _make_request(path="/api/tasks", method="GET", headers={"content-type": "text/html"})
        # Should not raise because method is GET
        mw._validate_headers(req)


# ---------------------------------------------------------------------------
# _validate_body() — Content-Length header validation only
# ---------------------------------------------------------------------------


class TestValidateBody:
    @pytest.mark.asyncio
    async def test_content_length_within_limit_passes(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-length": "100", "content-type": "application/json"})
        # Should not raise
        await mw._validate_body(req)

    @pytest.mark.asyncio
    async def test_content_length_at_limit_passes(self):
        mw = _make_middleware()
        req = _make_request(
            headers={
                "content-length": str(InputValidationMiddleware.MAX_BODY_SIZE),
                "content-type": "application/json",
            }
        )
        await mw._validate_body(req)

    @pytest.mark.asyncio
    async def test_content_length_exceeds_limit_raises(self):
        mw = _make_middleware()
        too_large = InputValidationMiddleware.MAX_BODY_SIZE + 1
        req = _make_request(
            headers={
                "content-length": str(too_large),
                "content-type": "application/json",
            }
        )
        with pytest.raises(ValueError, match="exceeds maximum"):
            await mw._validate_body(req)

    @pytest.mark.asyncio
    async def test_invalid_content_length_raises(self):
        mw = _make_middleware()
        req = _make_request(
            headers={"content-length": "not-a-number", "content-type": "application/json"}
        )
        with pytest.raises(ValueError, match="Invalid Content-Length"):
            await mw._validate_body(req)

    @pytest.mark.asyncio
    async def test_missing_content_length_passes(self):
        mw = _make_middleware()
        req = _make_request(headers={"content-type": "application/json"})
        # content-length not in headers — should pass
        req.headers = {"content-type": "application/json"}
        await mw._validate_body(req)


# ---------------------------------------------------------------------------
# _validate_url() — path and query validation
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_normal_path_passes(self):
        mw = _make_middleware()
        req = _make_request(path="/api/tasks/123")
        mw._validate_url(req)

    def test_path_too_long_raises(self):
        mw = _make_middleware()
        req = _make_request(path="/" + "a" * 2048)
        with pytest.raises(ValueError, match="too long"):
            mw._validate_url(req)

    def test_null_byte_in_path_raises(self):
        mw = _make_middleware()
        req = _make_request(path="/api/tasks\x00inject")
        with pytest.raises(ValueError, match="Invalid character"):
            mw._validate_url(req)

    def test_query_string_within_limit_passes(self):
        mw = _make_middleware()
        req = _make_request(query="key=value&other=data")
        mw._validate_url(req)

    def test_query_string_too_long_raises(self):
        mw = _make_middleware()
        req = _make_request(query="q=" + "a" * 4096)
        with pytest.raises(ValueError, match="too long"):
            mw._validate_url(req)

    def test_null_byte_in_query_raises(self):
        mw = _make_middleware()
        req = _make_request(query="key=value\x00inject")
        with pytest.raises(ValueError, match="Invalid character"):
            mw._validate_url(req)

    def test_path_traversal_attempt_raises(self):
        mw = _make_middleware()
        req = _make_request()
        req.url.__str__ = MagicMock(return_value="http://localhost/api/../secret")
        with pytest.raises(ValueError, match="Suspicious pattern"):
            mw._validate_url(req)

    def test_null_byte_encoding_raises(self):
        mw = _make_middleware()
        req = _make_request()
        req.url.__str__ = MagicMock(return_value="http://localhost/api/tasks%00inject")
        with pytest.raises(ValueError, match="Suspicious pattern"):
            mw._validate_url(req)


# ---------------------------------------------------------------------------
# dispatch() — end-to-end middleware behavior
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.asyncio
    async def test_valid_request_passes_through(self):
        mw = _make_middleware()
        req = _make_request(path="/api/tasks", method="POST")
        req.url.query = ""
        req.url.__str__ = MagicMock(return_value="http://localhost/api/tasks")

        resp = MagicMock()
        resp.status_code = 201

        async def call_next(r):
            return resp

        result = await mw.dispatch(req, call_next)
        assert result.status_code == 201

    @pytest.mark.asyncio
    async def test_validation_error_returns_400(self):
        mw = _make_middleware()
        req = _make_request(
            path="/api/tasks",
            method="POST",
            headers={"content-type": "text/xml"},
        )
        req.url.query = ""
        req.url.__str__ = MagicMock(return_value="http://localhost/api/tasks")


        async def call_next(r):
            return MagicMock()

        result = await mw.dispatch(req, call_next)
        assert result.status_code == 400

    @pytest.mark.asyncio
    async def test_get_requests_skip_body_validation(self):
        """GET requests don't validate body, just URL and headers."""
        mw = _make_middleware()
        req = _make_request(path="/api/tasks", method="GET")
        req.url.query = ""
        req.url.__str__ = MagicMock(return_value="http://localhost/api/tasks")

        resp = MagicMock()
        resp.status_code = 200

        async def call_next(r):
            return resp

        result = await mw.dispatch(req, call_next)
        assert result.status_code == 200
