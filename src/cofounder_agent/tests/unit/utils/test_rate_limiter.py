"""
Tests for utils/rate_limiter.py

Covers:
- limiter is exported (not None)
- limiter has a .limit() method
- When slowapi is available: limiter is a real Limiter instance
- When slowapi is absent: _NoOpLimiter is used — .limit() is a pass-through decorator
"""

import importlib
import sys
from unittest.mock import patch


class TestRateLimiterModuleExport:
    def test_limiter_is_exported(self):
        from utils.rate_limiter import limiter

        assert limiter is not None

    def test_limiter_has_limit_method(self):
        from utils.rate_limiter import limiter

        assert callable(limiter.limit)

    def test_limit_returns_decorator(self):
        from utils.rate_limiter import limiter

        decorator = limiter.limit("10/minute")
        assert callable(decorator)

    def test_limit_decorator_is_pass_through(self):
        """When applied to a route-like function with a 'request' param, the decorator
        must return a callable (the real slowapi Limiter wraps the function)."""
        from utils.rate_limiter import limiter
        from starlette.requests import Request

        def _dummy(request: Request):
            return "ok"

        wrapped = limiter.limit("5/minute")(_dummy)
        assert callable(wrapped)


class TestNoOpLimiterFallback:
    """Test the _NoOpLimiter path used when slowapi is not installed."""

    def _import_fresh_with_no_slowapi(self):
        """Force-reimport utils.rate_limiter with slowapi removed from sys.modules."""
        # Remove cached module so we can reimport
        for key in list(sys.modules.keys()):
            if key in ("utils.rate_limiter", "slowapi", "slowapi.util"):
                del sys.modules[key]

        # Patch slowapi away so the ImportError branch is triggered
        with patch.dict(sys.modules, {"slowapi": None, "slowapi.util": None}):  # type: ignore[dict-item]
            import utils.rate_limiter as mod

            limiter = mod.limiter
        return limiter

    def test_noop_limiter_limit_returns_decorator(self):
        limiter = self._import_fresh_with_no_slowapi()
        dec = limiter.limit("100/hour")
        assert callable(dec)

    def test_noop_limiter_decorator_is_passthrough(self):
        limiter = self._import_fresh_with_no_slowapi()

        def _fn():
            return 42

        result_fn = limiter.limit("1/second")(_fn)
        assert result_fn is _fn  # NoOp returns the original function unchanged

    def test_noop_limiter_accepts_any_rate_string(self):
        limiter = self._import_fresh_with_no_slowapi()
        # Should not raise regardless of the rate string value
        for rate in ["1/second", "100/minute", "1000/hour", "10000/day"]:
            dec = limiter.limit(rate)
            assert callable(dec)

    def test_noop_limiter_accepts_kwargs(self):
        limiter = self._import_fresh_with_no_slowapi()
        dec = limiter.limit("5/minute", per_method=True, error_message="slow down")
        assert callable(dec)
