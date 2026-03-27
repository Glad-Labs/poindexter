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
