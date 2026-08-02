"""
Tests for utils/middleware_config.py

Covers:
- MiddlewareConfig.__init__: limiter and profiling_middleware are None
- create_middleware_config: returns MiddlewareConfig instance
- module-level singleton: middleware_config is a MiddlewareConfig
- get_limiter: returns self.limiter
- _setup_cors: reads ALLOWED_ORIGINS env var, strips whitespace/trailing slashes
- _setup_cors: splits multiple origins by comma
- _setup_rate_limiting: sets self.limiter and app.state.limiter when slowapi available
- _setup_rate_limiting: sets limiter to None when slowapi import fails
- register_all_middleware: calls all setup methods without raising
- Individual _setup_* methods: call app.add_middleware (or equivalent)
"""

import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from utils.middleware_config import MiddlewareConfig, create_middleware_config, middleware_config


def _make_app():
    app = MagicMock()
    app.add_middleware = MagicMock()
    app.exception_handler = MagicMock(return_value=lambda fn: fn)
    app.state = MagicMock()
    return app


class TestMiddlewareConfigInit:
    def test_initial_limiter_is_none(self):
        mc = MiddlewareConfig()
        assert mc.limiter is None

    def test_initial_profiling_middleware_is_none(self):
        mc = MiddlewareConfig()
        assert mc.profiling_middleware is None


class TestCreateMiddlewareConfig:
    def test_returns_middleware_config_instance(self):
        result = create_middleware_config()
        assert isinstance(result, MiddlewareConfig)

    def test_each_call_returns_new_instance(self):
        a = create_middleware_config()
        b = create_middleware_config()
        assert a is not b


class TestModuleLevelSingleton:
    def test_singleton_is_middleware_config_type(self):
        assert isinstance(middleware_config, MiddlewareConfig)


class TestGetLimiter:
    def test_returns_none_when_not_set(self):
        mc = MiddlewareConfig()
        assert mc.get_limiter() is None

    def test_returns_limiter_when_set(self):
        mc = MiddlewareConfig()
        mock_limiter = MagicMock()
        mc.limiter = mock_limiter
        assert mc.get_limiter() is mock_limiter


class TestSetupCors:
    def test_default_origins_used_when_env_not_set(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOWED_ORIGINS", None)
            mc._setup_cors(app)

        app.add_middleware.assert_called_once()
        _, kwargs = app.add_middleware.call_args
        origins = kwargs.get("allow_origins", [])
        # Default includes localhost:3000
        assert any("localhost:3000" in o for o in origins)

    def test_env_origins_override_defaults(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            os.environ, {"ALLOWED_ORIGINS": "https://example.com,https://app.example.com"}
        ):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        origins = kwargs.get("allow_origins", [])
        assert "https://example.com" in origins
        assert "https://app.example.com" in origins

    def test_origins_have_whitespace_stripped(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": " https://a.com , https://b.com "}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        origins = kwargs.get("allow_origins", [])
        assert "https://a.com" in origins
        assert "https://b.com" in origins
        assert not any(o.startswith(" ") for o in origins)

    def test_origins_have_trailing_slashes_stripped(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "https://a.com/,https://b.com/"}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        origins = kwargs.get("allow_origins", [])
        assert "https://a.com" in origins
        assert "https://b.com" in origins

    def test_single_origin_is_parsed_correctly(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "https://single.com"}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        origins = kwargs.get("allow_origins", [])
        assert "https://single.com" in origins
        assert len(origins) == 1

    def test_cors_allows_credentials(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "https://a.com"}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        assert kwargs.get("allow_credentials") is True

    def test_cors_restricts_methods(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "https://a.com"}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        methods = kwargs.get("allow_methods", [])
        assert "GET" in methods
        assert "POST" in methods
        # The wildcard "*" must NOT be present — security requirement from #220
        assert "*" not in methods

    def test_cors_explicit_headers_no_wildcard(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(os.environ, {"ALLOWED_ORIGINS": "https://a.com"}):
            mc._setup_cors(app)

        _, kwargs = app.add_middleware.call_args
        headers = kwargs.get("allow_headers", [])
        # Must NOT be wildcard per security requirement #220
        assert headers != ["*"]
        assert "Authorization" in headers
        assert "Content-Type" in headers


class TestSetupRateLimiting:
    def test_sets_limiter_on_app_state_when_slowapi_available(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_rate_limiting(app)
        # If slowapi installed: limiter set on app state
        if mc.limiter is not None:
            assert app.state.limiter is mc.limiter

    def test_limiter_none_when_slowapi_not_installed(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            "sys.modules",
            {"slowapi": None, "slowapi.errors": None, "slowapi.util": None},  # type: ignore[dict-item]
        ):
            mc._setup_rate_limiting(app)
        # Without slowapi, limiter should remain None
        assert mc.limiter is None

    def test_does_not_raise_when_slowapi_missing(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            "sys.modules",
            {"slowapi": None, "slowapi.errors": None, "slowapi.util": None},  # type: ignore[dict-item]
        ):
            mc._setup_rate_limiting(app)  # should not raise


class TestSetupCacheControl:
    def test_adds_cache_control_middleware(self):
        from middleware.cache_control import CacheControlMiddleware

        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_cache_control(app)
        app.add_middleware.assert_called_once_with(CacheControlMiddleware)


class TestSetupInputValidation:
    def test_adds_input_validation_middlewares(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_input_validation(app)
        # Should have called add_middleware twice (Payload + Input)
        assert app.add_middleware.call_count == 2

    def test_does_not_raise_when_module_missing(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            "sys.modules",
            {"middleware.input_validation": None},  # type: ignore[dict-item]
        ):
            mc._setup_input_validation(app)  # must not raise


class TestSetupTokenValidation:
    def test_adds_token_validation_middleware(self):
        from middleware.token_validation import TokenValidationMiddleware

        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_token_validation(app)
        app.add_middleware.assert_called_once_with(TokenValidationMiddleware)

    def test_does_not_raise_when_module_missing(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            "sys.modules",
            {"middleware.token_validation": None},  # type: ignore[dict-item]
        ):
            mc._setup_token_validation(app)  # must not raise


class TestSetupRequestId:
    def test_adds_request_id_middleware(self):
        from middleware.request_id import RequestIDMiddleware

        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_request_id(app)
        app.add_middleware.assert_called_once_with(RequestIDMiddleware)


class TestSetupSecurityHeaders:
    def test_adds_security_headers_middleware(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_security_headers(app)
        app.add_middleware.assert_called_once()


class TestSetupProfiling:
    def test_adds_profiling_middleware_when_available(self):
        from middleware.profiling_middleware import ProfilingMiddleware

        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_profiling(app)
        # add_middleware should have been called with ProfilingMiddleware
        call_args = [c.args[0] for c in app.add_middleware.call_args_list]
        assert ProfilingMiddleware in call_args

    def test_stores_profiling_middleware_reference_on_app_state(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc._setup_profiling(app)
        # app.state.profiling_middleware should be set
        assert app.state.profiling_middleware is not None

    def test_does_not_raise_when_module_missing(self):
        mc = MiddlewareConfig()
        app = _make_app()
        with patch.dict(
            "sys.modules",
            {"middleware.profiling_middleware": None},  # type: ignore[dict-item]
        ):
            mc._setup_profiling(app)  # must not raise


class TestRegisterAllMiddleware:
    def test_does_not_raise(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc.register_all_middleware(app)  # should not raise

    def test_calls_multiple_add_middleware(self):
        mc = MiddlewareConfig()
        app = _make_app()
        mc.register_all_middleware(app)
        # Multiple middlewares are registered
        assert app.add_middleware.call_count >= 3


# ---------------------------------------------------------------------------
# Composed stack — the REAL middleware chain on a real FastAPI app.
#
# Cache-Control must be outermost of every response-PRODUCING middleware so
# that short-circuited responses (auth 401s, validation 400s, CORS preflights)
# are stamped too. When it sat innermost, all of those escaped with no
# Cache-Control at all and fell back to browser heuristic caching — measured
# live 2026-08-01 (stack#3000's known-boundary section, closed by this
# reorder). Mock-based tests can't see ordering, hence the real app here.
# ---------------------------------------------------------------------------


def _real_stacked_app():
    from fastapi import FastAPI

    app = FastAPI()
    MiddlewareConfig().register_all_middleware(app)

    @app.get("/api/tasks/probe")
    async def _probe():
        return {"ok": True}

    return app


def _stack_names(app):
    """user_middleware class names, outermost first."""
    return [m.cls.__name__ for m in app.user_middleware]


class TestComposedStackOrdering:
    def test_cache_control_wraps_every_response_producer(self):
        names = _stack_names(_real_stacked_app())
        cache_pos = names.index("CacheControlMiddleware")
        # Smaller index = more outer. Every middleware that can mint its own
        # response must sit INSIDE cache-control or its responses go
        # unstamped again.
        for producer in (
            "CORSMiddleware",
            "TokenValidationMiddleware",
            "InputValidationMiddleware",
            "PayloadInspectionMiddleware",
        ):
            assert cache_pos < names.index(producer), (
                f"CacheControlMiddleware (pos {cache_pos}) must be outside "
                f"{producer} (pos {names.index(producer)}); stack: {names}"
            )

    def test_only_the_red_timer_sits_outside_cache_control(self):
        # Prometheus stays outermost so RED latency captures full wall-clock;
        # that's safe only because it is pure observation. Anything else
        # migrating outside cache-control would open the unstamped-response
        # hole again.
        names = _stack_names(_real_stacked_app())
        outside = names[: names.index("CacheControlMiddleware")]
        assert outside == ["PrometheusMetricsMiddleware"], (
            f"unexpected middleware outside CacheControl: {outside}"
        )


class TestComposedStackShortCircuits:
    """The three response-minting middlewares, triggered for real."""

    def test_token_validation_401_is_stamped_no_store(self):
        # /api/tasks is in PROTECTED_PATHS; no Authorization header → the 401
        # comes from TokenValidationMiddleware, never reaching a route. This
        # exact response carried NO Cache-Control before the reorder.
        client = TestClient(_real_stacked_app())
        resp = client.get("/api/tasks/probe")

        assert resp.status_code == 401
        assert resp.headers["cache-control"] == "no-store"

    def test_malformed_bearer_401_is_stamped_no_store(self):
        client = TestClient(_real_stacked_app())
        resp = client.get("/api/tasks/probe", headers={"Authorization": "Basic xyz"})

        assert resp.status_code == 401
        assert resp.headers["cache-control"] == "no-store"

    def test_cors_preflight_is_stamped_and_still_functional(self):
        # Preflights are answered by CORSMiddleware directly. Stamping them
        # no-store must not break preflight caching: the browser's preflight
        # cache is a separate cache governed by Access-Control-Max-Age (Fetch
        # spec), not by Cache-Control — so both headers coexisting is correct.
        client = TestClient(_real_stacked_app())
        resp = client.options(
            "/api/tasks/probe",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        # CORS still fully answered the preflight through the outer stamp
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert resp.headers["access-control-max-age"] == "600"
        assert "GET" in resp.headers["access-control-allow-methods"]

    def test_route_response_still_stamped_and_vary_merges_with_cors(self):
        # End-to-end through the whole real chain: a cacheable opt-in path
        # keeps its directive, and CORS's Vary: Origin (inner) merges with
        # Vary: Authorization (outer) instead of either clobbering the other.
        app = _real_stacked_app()

        @app.get("/api/capabilities/probe")
        async def _caps():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get(
            "/api/capabilities/probe", headers={"Origin": "http://localhost:3000"}
        )

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "public, max-age=300"
        vary = {v.strip() for v in resp.headers["vary"].split(",")}
        assert {"Origin", "Authorization"} <= vary
