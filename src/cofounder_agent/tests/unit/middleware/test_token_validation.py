"""
Unit tests for middleware/token_validation.py — TokenValidationMiddleware
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from middleware.token_validation import TokenValidationMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path="/api/tasks",
    method="GET",
    auth_header=None,
    upgrade_header=None,
):
    req = MagicMock()
    req.url.path = path
    req.method = method
    headers = {}
    if auth_header is not None:
        headers["Authorization"] = auth_header
    if upgrade_header is not None:
        headers["upgrade"] = upgrade_header
    req.headers = headers
    return req


def _make_mw():
    app = MagicMock()
    return TokenValidationMiddleware(app)


# ---------------------------------------------------------------------------
# Dev bypass — DISABLE_AUTH_FOR_DEV=true
# ---------------------------------------------------------------------------


class TestDevBypass:
    @pytest.mark.asyncio
    async def test_disable_auth_env_bypasses_all_checks(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks")  # protected, no auth header

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "true"}):
            await mw.dispatch(req, call_next)

        assert called

    @pytest.mark.asyncio
    async def test_disable_auth_false_does_not_bypass(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks")  # protected, no auth header

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, call_next)

        # Should return 401, not pass through
        assert not called
        assert result.status_code == 401


# ---------------------------------------------------------------------------
# WebSocket bypass
# ---------------------------------------------------------------------------


class TestWebSocketBypass:
    @pytest.mark.asyncio
    async def test_websocket_upgrade_bypasses_token_check(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks", upgrade_header="websocket")

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            await mw.dispatch(req, call_next)

        assert called


# ---------------------------------------------------------------------------
# Public paths bypass
# ---------------------------------------------------------------------------


class TestPublicPaths:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/github",
            "/api/public/tasks",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/ws/workflow",
        ],
    )
    async def test_public_path_passes_without_token(self, path):
        mw = _make_mw()
        req = _make_request(path=path)

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            await mw.dispatch(req, call_next)

        assert called


# ---------------------------------------------------------------------------
# Non-protected paths pass through without token
# ---------------------------------------------------------------------------


class TestNonProtectedPaths:
    @pytest.mark.asyncio
    async def test_unclassified_path_passes_without_token(self):
        mw = _make_mw()
        req = _make_request(path="/api/custom-endpoint-not-in-list")

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            await mw.dispatch(req, call_next)

        assert called


# ---------------------------------------------------------------------------
# Protected paths — token validation
# ---------------------------------------------------------------------------


class TestProtectedPaths:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/tasks",
            "/api/workflows",
            "/api/custom-workflows",
            "/api/agents",
            "/api/capability-tasks",
            "/api/bulk-tasks",
        ],
    )
    async def test_protected_path_without_token_returns_401(self, path):
        mw = _make_mw()
        req = _make_request(path=path)  # no auth header

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, AsyncMock())

        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_token_present_passes_through(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks", auth_header="Bearer valid-jwt-token")

        called = []

        async def call_next(r):
            called.append(True)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, call_next)

        assert called
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_malformed_auth_header_returns_401(self):
        """Authorization header present but doesn't start with 'Bearer '."""
        mw = _make_mw()
        req = _make_request(path="/api/tasks", auth_header="Basic dXNlcjpwYXNz")

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, AsyncMock())

        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_token_without_bearer_prefix_returns_401(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks", auth_header="just-a-token")

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, AsyncMock())

        assert result.status_code == 401


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_500(self):
        mw = _make_mw()
        req = _make_request(path="/api/tasks", auth_header="Bearer token")

        async def call_next(r):
            raise RuntimeError("Internal boom")

        with patch.dict("os.environ", {"DISABLE_AUTH_FOR_DEV": "false"}):
            result = await mw.dispatch(req, call_next)

        assert result.status_code == 500
