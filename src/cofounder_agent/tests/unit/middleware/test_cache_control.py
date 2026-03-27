"""
Unit tests for middleware/cache_control.py — _cache_directive and CacheControlMiddleware
"""

from unittest.mock import MagicMock

import pytest

from middleware.cache_control import (
    _PRIVATE_MAX_AGE,
    _PUBLIC_MAX_AGE,
    CacheControlMiddleware,
    _cache_directive,
)

# ---------------------------------------------------------------------------
# _cache_directive() — pure function, no ASGI needed
# ---------------------------------------------------------------------------


class TestCacheDirective:
    # --- Mutations always no-store ---

    def test_post_is_no_store(self):
        assert _cache_directive("/api/tasks", "POST") == "no-store"

    def test_put_is_no_store(self):
        assert _cache_directive("/api/tasks/123", "PUT") == "no-store"

    def test_patch_is_no_store(self):
        assert _cache_directive("/api/tasks/123", "PATCH") == "no-store"

    def test_delete_is_no_store(self):
        assert _cache_directive("/api/tasks/123", "DELETE") == "no-store"

    # --- No-store paths (GET) ---

    def test_auth_path_is_no_store(self):
        assert _cache_directive("/api/auth/login", "GET") == "no-store"

    def test_oauth_path_is_no_store(self):
        assert _cache_directive("/api/oauth/github", "GET") == "no-store"

    def test_token_path_is_no_store(self):
        assert _cache_directive("/api/token/refresh", "GET") == "no-store"

    def test_ws_path_is_no_store(self):
        assert _cache_directive("/ws/workflow-progress/123", "GET") == "no-store"

    def test_dev_path_is_no_store(self):
        assert _cache_directive("/dev/tasks", "GET") == "no-store"

    # --- Public paths (GET) ---

    def test_posts_path_is_public(self):
        result = _cache_directive("/api/posts", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    def test_cms_path_is_public(self):
        result = _cache_directive("/api/cms/articles", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    def test_analytics_path_is_public(self):
        result = _cache_directive("/api/analytics/summary", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    def test_agents_status_is_public(self):
        result = _cache_directive("/api/agents/status", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    def test_health_is_public(self):
        result = _cache_directive("/health", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    def test_api_health_is_public(self):
        result = _cache_directive("/api/health", "GET")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"

    # --- Private paths (GET) ---

    def test_tasks_get_is_private(self):
        result = _cache_directive("/api/tasks", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    def test_workflows_get_is_private(self):
        result = _cache_directive("/api/workflows", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    def test_user_get_is_private(self):
        result = _cache_directive("/api/user/profile", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    def test_approvals_get_is_private(self):
        result = _cache_directive("/api/approvals", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    def test_settings_get_is_private(self):
        result = _cache_directive("/api/settings", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    # --- Unknown path falls back to private ---

    def test_unknown_path_is_private_default(self):
        result = _cache_directive("/api/unknown-endpoint", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    def test_root_path_is_private_default(self):
        result = _cache_directive("/", "GET")
        assert result == f"private, max-age={_PRIVATE_MAX_AGE}"

    # --- HEAD method treated same as GET ---

    def test_head_on_public_path_is_public(self):
        result = _cache_directive("/api/posts", "HEAD")
        assert result == f"public, max-age={_PUBLIC_MAX_AGE}"


# ---------------------------------------------------------------------------
# CacheControlMiddleware — stamps header on responses that lack it
# ---------------------------------------------------------------------------


def _make_request(path="/api/tasks", method="GET"):
    """Build a minimal mock Request."""
    req = MagicMock()
    req.url.path = path
    req.method = method
    return req


def _make_response(existing_cache_control=None):
    """Build a minimal mock Response."""
    headers = {}
    if existing_cache_control:
        headers["cache-control"] = existing_cache_control
    resp = MagicMock()
    resp.headers = headers
    return resp


class TestCacheControlMiddleware:
    @pytest.mark.asyncio
    async def test_adds_cache_control_when_missing(self):
        app = MagicMock()
        mw = CacheControlMiddleware(app)

        request = _make_request("/api/tasks", "GET")
        response = _make_response()  # no cache-control header

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        # Middleware sets "Cache-Control" (title-case) on the headers dict
        assert "Cache-Control" in result.headers
        assert "private" in result.headers["Cache-Control"]

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_cache_control(self):
        app = MagicMock()
        mw = CacheControlMiddleware(app)

        request = _make_request("/api/posts", "GET")
        # Use lowercase key so middleware's `"cache-control" not in response.headers` is False
        response = _make_response(existing_cache_control="no-cache, no-store")

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        # The existing header should be untouched
        assert result.headers["cache-control"] == "no-cache, no-store"
        assert "Cache-Control" not in result.headers  # no duplicate

    @pytest.mark.asyncio
    async def test_mutation_gets_no_store(self):
        app = MagicMock()
        mw = CacheControlMiddleware(app)

        request = _make_request("/api/tasks", "POST")
        response = _make_response()

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        assert result.headers["Cache-Control"] == "no-store"

    @pytest.mark.asyncio
    async def test_public_path_get_gets_public_cache(self):
        app = MagicMock()
        mw = CacheControlMiddleware(app)

        request = _make_request("/api/posts", "GET")
        response = _make_response()

        async def call_next(req):
            return response

        result = await mw.dispatch(request, call_next)
        assert result.headers["Cache-Control"] == f"public, max-age={_PUBLIC_MAX_AGE}"
