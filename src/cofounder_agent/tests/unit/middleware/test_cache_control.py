"""
Unit tests for middleware/cache_control.py — _cache_directive, Vary, and
CacheControlMiddleware.

The policy is **deny-by-default**: anything not explicitly opted in is
``no-store``. These tests pin that inversion, the two live-measured bugs that
motivated it (console live-panel staleness and a 5-minute cache on /api/health),
and the Vary merge that keeps one token's response from being served to
another.
"""

from unittest.mock import MagicMock

import pytest
from starlette.datastructures import MutableHeaders

from middleware.cache_control import (
    _STATIC_MAX_AGE,
    NO_STORE,
    REVALIDATE,
    CacheControlMiddleware,
    _add_vary_authorization,
    _cache_directive,
    _is_storable,
)

# ---------------------------------------------------------------------------
# _cache_directive() — pure function, no ASGI needed
# ---------------------------------------------------------------------------


class TestCacheDirectiveMethods:
    """Only GET/HEAD are cacheable at all."""

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_mutations_are_no_store(self, method):
        assert _cache_directive("/api/tasks", method) == NO_STORE

    def test_exotic_method_is_no_store(self):
        # The old rule enumerated mutations and let everything else fall
        # through to a cacheable default; this one inverts that.
        assert _cache_directive("/api/templates", "PROPFIND") == NO_STORE

    def test_head_is_treated_like_get(self):
        assert _cache_directive("/api/capabilities", "HEAD") == _cache_directive(
            "/api/capabilities", "GET"
        )


class TestCacheDirectiveNoStorePaths:
    """Auth/socket/dev paths stay explicitly no-store."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/login",
            "/api/oauth/github",
            "/api/token/refresh",
            "/ws/workflow-progress/123",
            "/dev/tasks",
        ],
    )
    def test_sensitive_paths_are_no_store(self, path):
        assert _cache_directive(path, "GET") == NO_STORE


class TestCacheDirectiveDenyByDefault:
    """The inversion: unclassified means uncached, not cached-for-60s."""

    def test_unknown_path_is_no_store(self):
        assert _cache_directive("/api/unknown-endpoint", "GET") == NO_STORE

    def test_root_path_is_no_store(self):
        assert _cache_directive("/", "GET") == NO_STORE

    def test_a_brand_new_route_is_uncached_not_stale(self):
        # The point of the inversion. A route nobody classified used to
        # inherit `private, max-age=60` silently; the failure mode for
        # forgetting is now "slightly slower", never "quietly wrong".
        assert _cache_directive("/api/some-feature-shipped-next-year", "GET") == NO_STORE

    @pytest.mark.parametrize(
        "path",
        [
            "/api/activity",  # console polls every 3s
            "/api/trace/active",  # every 30s
            "/api/trace/abc-123",  # every 6s
            "/api/gpu/queue",  # every 10s
            "/api/logs",  # every 10s
        ],
    )
    def test_console_live_panels_are_never_cached(self, path):
        # Measured in a real browser 2026-08-01: under the old catch-all these
        # returned transferSize=0 on repeat polls — the browser answered from
        # cache and the "live" panel sat on minute-old data. A Bearer header
        # does not stop a private cache from doing that.
        assert _cache_directive(path, "GET") == NO_STORE

    @pytest.mark.parametrize("path", ["/health", "/api/health"])
    def test_health_is_never_cached(self, path):
        # Was `public, max-age=300` while the console polls it every 30s. While
        # an entry is fresh the browser issues NO request, so a dead worker
        # still read "healthy" for up to five minutes.
        assert _cache_directive(path, "GET") == NO_STORE

    @pytest.mark.parametrize(
        "path", ["/api/tasks", "/api/settings", "/api/approvals", "/api/posts"]
    )
    def test_mutable_operator_data_is_never_cached(self, path):
        # These read back what the operator just changed. Any freshness window
        # is a window where an edit looks like it didn't stick.
        assert _cache_directive(path, "GET") == NO_STORE


class TestCacheDirectiveOptIns:
    """The short list that still earns a cache."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/capabilities",
            "/api/agent-registry",
            "/api/templates",
        ],
    )
    def test_static_catalogs_are_public_cached(self, path):
        assert _cache_directive(path, "GET") == f"public, max-age={_STATIC_MAX_AGE}"

    def test_well_known_is_left_to_the_oauth_sdk(self):
        # Verified live: the SDK sets its own `public, max-age=3600` on OAuth
        # discovery docs, so the middleware defers and an allowlist entry here
        # would be inert — and would advertise a TTL that isn't the real one.
        assert _cache_directive("/.well-known/openid-configuration", "GET") == NO_STORE

    def test_file_route_revalidates_rather_than_expiring(self):
        # FileResponse carries an ETag, so `no-cache` costs a bodiless 304 and
        # can never serve a stale body — strictly better than no-store here,
        # which would re-send the image every time.
        assert _cache_directive("/images/generated/abc.png", "GET") == REVALIDATE


# ---------------------------------------------------------------------------
# Vary
# ---------------------------------------------------------------------------


class TestIsStorable:
    @pytest.mark.parametrize(
        "cc,expected",
        [
            ("no-store", False),
            ("private, no-cache, no-store, must-revalidate", False),
            ("no-cache", True),  # stores, then revalidates — still needs Vary
            ("public, max-age=300", True),
            ("private, max-age=60", True),
        ],
    )
    def test_only_no_store_forbids_storage(self, cc, expected):
        assert _is_storable(cc) is expected


class TestAddVaryAuthorization:
    def test_sets_vary_when_absent(self):
        h = MutableHeaders()
        _add_vary_authorization(h)
        assert h["vary"] == "Authorization"

    def test_merges_with_existing_vary(self):
        # CORSMiddleware runs INSIDE this one, so its `Vary: Origin` is
        # already on the response by the time we stamp; a clobber would drop it.
        h = MutableHeaders({"vary": "Origin"})
        _add_vary_authorization(h)
        assert h["vary"] == "Origin, Authorization"

    def test_does_not_duplicate(self):
        h = MutableHeaders({"vary": "Authorization"})
        _add_vary_authorization(h)
        assert h["vary"] == "Authorization"

    def test_is_case_insensitive_about_existing_entry(self):
        h = MutableHeaders({"vary": "authorization"})
        _add_vary_authorization(h)
        assert h["vary"] == "authorization"

    def test_leaves_vary_star_alone(self):
        # `*` already means "never reuse this"; narrowing it would be a downgrade.
        h = MutableHeaders({"vary": "*"})
        _add_vary_authorization(h)
        assert h["vary"] == "*"


# ---------------------------------------------------------------------------
# CacheControlMiddleware
# ---------------------------------------------------------------------------


def _make_request(path="/api/tasks", method="GET"):
    """Build a minimal mock Request."""
    req = MagicMock()
    req.url.path = path
    req.method = method
    return req


def _make_response(headers=None):
    """A response object whose .headers behaves like Starlette's."""
    resp = MagicMock()
    resp.headers = MutableHeaders(headers or {})
    return resp


async def _dispatch(path, method="GET", response_headers=None):
    mw = CacheControlMiddleware(MagicMock())
    response = _make_response(response_headers)

    async def call_next(_req):
        return response

    return await mw.dispatch(_make_request(path, method), call_next)


class TestCacheControlMiddleware:
    @pytest.mark.asyncio
    async def test_stamps_no_store_on_unclassified_path(self):
        result = await _dispatch("/api/unknown")
        assert result.headers["cache-control"] == NO_STORE

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_cache_control(self):
        # The seam utils/operator_console.py depends on: the console mount sets
        # its own `no-cache`, and this middleware must stand down.
        result = await _dispatch(
            "/console/js/app.jsx", response_headers={"cache-control": "no-cache"}
        )
        assert result.headers["cache-control"] == "no-cache"

    @pytest.mark.asyncio
    async def test_mutation_gets_no_store(self):
        result = await _dispatch("/api/tasks", "POST")
        assert result.headers["cache-control"] == NO_STORE

    @pytest.mark.asyncio
    async def test_static_catalog_gets_public_cache(self):
        result = await _dispatch("/api/capabilities")
        assert result.headers["cache-control"] == f"public, max-age={_STATIC_MAX_AGE}"

    @pytest.mark.asyncio
    async def test_no_store_response_gets_no_vary(self):
        # Nothing stores it, so a Vary would be dead weight on every response.
        result = await _dispatch("/api/activity")
        assert "vary" not in result.headers

    @pytest.mark.asyncio
    async def test_cacheable_response_gets_vary_authorization(self):
        result = await _dispatch("/api/capabilities")
        assert result.headers["vary"] == "Authorization"

    @pytest.mark.asyncio
    async def test_vary_applies_to_a_route_set_cache_control_too(self):
        # A route opting into a long max-age (podcast/video episodes use
        # `public, max-age=86400`) is exactly where a missing Vary hurts most,
        # and the middleware never touches its Cache-Control.
        result = await _dispatch(
            "/api/podcast/episodes/abc.mp3",
            response_headers={"cache-control": "public, max-age=86400"},
        )
        assert result.headers["cache-control"] == "public, max-age=86400"
        assert result.headers["vary"] == "Authorization"

    @pytest.mark.asyncio
    async def test_vary_merges_rather_than_clobbers(self):
        result = await _dispatch("/api/capabilities", response_headers={"vary": "Origin"})
        assert result.headers["vary"] == "Origin, Authorization"
