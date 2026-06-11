"""
Tests for utils/rate_limiter.py

Covers:
- limiter is exported (not None)
- limiter has a .limit() method
- When slowapi is available: limiter is a real Limiter instance
- When slowapi is absent: _NoOpLimiter is used — .limit() is a pass-through decorator
"""

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
        from starlette.requests import Request

        from utils.rate_limiter import limiter

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


class TestSettingsLimit:
    """`_settings_limit` — zero-arg slowapi callable backed by configure_rate_limiter().

    slowapi 0.1.9 calls dynamic-limit callables with NO arguments (the
    callable can only receive the key string if named 'key').  _settings_limit
    therefore reads from a module-level _site_config reference wired at app
    startup.  Tests wire it directly via configure_rate_limiter().
    """

    def setup_method(self):
        """Reset the module-level _site_config before each test."""
        import utils.rate_limiter as mod
        mod._site_config = None

    def teardown_method(self):
        import utils.rate_limiter as mod
        mod._site_config = None

    def test_returns_callable(self):
        from utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        assert callable(fn)

    def test_callable_takes_no_args(self):
        import inspect
        from utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        params = inspect.signature(fn).parameters
        assert len(params) == 0

    def test_name_includes_setting_key(self):
        from utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        assert "rate_limit_token_per_ip" in fn.__name__

    def test_reads_from_site_config(self):
        from unittest.mock import MagicMock

        from utils.rate_limiter import _settings_limit, configure_rate_limiter

        sc = MagicMock()
        sc.get.return_value = "3/second"
        configure_rate_limiter(sc)

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        result = fn()

        assert result == "3/second"
        sc.get.assert_called_once_with("rate_limit_token_per_ip", "10/minute")

    def test_falls_back_to_default_when_site_config_not_wired(self):
        """When configure_rate_limiter() hasn't been called (e.g. in tests),
        the callable returns the hardcoded default."""
        from utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        result = fn()

        assert result == "10/minute"

    def test_falls_back_to_default_when_get_raises(self):
        from unittest.mock import MagicMock

        from utils.rate_limiter import _settings_limit, configure_rate_limiter

        sc = MagicMock()
        sc.get.side_effect = RuntimeError("db unavailable")
        configure_rate_limiter(sc)

        fn = _settings_limit("rate_limit_triage_per_ip", "20/minute")
        result = fn()

        assert result == "20/minute"

    def test_each_key_gets_independent_callable(self):
        from unittest.mock import MagicMock

        from utils.rate_limiter import _settings_limit, configure_rate_limiter

        fn_token = _settings_limit("rate_limit_token_per_ip", "10/minute")
        fn_triage = _settings_limit("rate_limit_triage_per_ip", "20/minute")

        sc = MagicMock()
        sc.get.side_effect = lambda k, d: {"rate_limit_token_per_ip": "2/minute",
                                            "rate_limit_triage_per_ip": "5/minute"}.get(k, d)
        configure_rate_limiter(sc)

        assert fn_token() == "2/minute"
        assert fn_triage() == "5/minute"

    def test_configure_rate_limiter_wires_instance(self):
        from unittest.mock import MagicMock

        import utils.rate_limiter as mod
        from utils.rate_limiter import configure_rate_limiter

        sc = MagicMock()
        assert mod._site_config is None
        configure_rate_limiter(sc)
        assert mod._site_config is sc
