"""
Tests for utils/rate_limiter.py

Covers:
- limiter is exported (not None)
- limiter has a .limit() method
- When slowapi is available: limiter is a real Limiter instance
- When slowapi is absent: _NoOpLimiter is used — .limit() is a pass-through decorator
- Every seeded rate_limit_* key has a _settings_limit reader, and every reader's
  key is seeded with the same default (TestSeededLimitsMatchReaders)
"""

import ast
import functools
import sys
from pathlib import Path
from unittest.mock import patch

from tests.unit._nonempty import nonempty


class TestRateLimiterModuleExport:
    def test_limiter_is_exported(self):
        from poindexter.utils.rate_limiter import limiter

        assert limiter is not None

    def test_limiter_has_limit_method(self):
        from poindexter.utils.rate_limiter import limiter

        assert callable(limiter.limit)

    def test_limit_returns_decorator(self):
        from poindexter.utils.rate_limiter import limiter

        decorator = limiter.limit("10/minute")
        assert callable(decorator)

    def test_limit_decorator_is_pass_through(self):
        """When applied to a route-like function with a 'request' param, the decorator
        must return a callable (the real slowapi Limiter wraps the function)."""
        from starlette.requests import Request

        from poindexter.utils.rate_limiter import limiter

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
            # flat + canonical spelling (one module, two names since poindexter#1046 step 2)
            if key in ("poindexter.utils.rate_limiter", "poindexter.utils.rate_limiter", "slowapi", "slowapi.util"):
                del sys.modules[key]

        # Patch slowapi away so the ImportError branch is triggered
        with patch.dict(sys.modules, {"slowapi": None, "slowapi.util": None}):  # type: ignore[dict-item]
            import poindexter.utils.rate_limiter as mod

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
        import poindexter.utils.rate_limiter as mod
        mod._site_config = None

    def teardown_method(self):
        import poindexter.utils.rate_limiter as mod
        mod._site_config = None

    def test_returns_callable(self):
        from poindexter.utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        assert callable(fn)

    def test_callable_takes_no_args(self):
        import inspect

        from poindexter.utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        params = inspect.signature(fn).parameters
        assert len(params) == 0

    def test_name_includes_setting_key(self):
        from poindexter.utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        assert "rate_limit_token_per_ip" in fn.__name__

    def test_reads_from_site_config(self):
        from unittest.mock import MagicMock

        from poindexter.utils.rate_limiter import _settings_limit, configure_rate_limiter

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
        from poindexter.utils.rate_limiter import _settings_limit

        fn = _settings_limit("rate_limit_token_per_ip", "10/minute")
        result = fn()

        assert result == "10/minute"

    def test_falls_back_to_default_when_get_raises(self):
        from unittest.mock import MagicMock

        from poindexter.utils.rate_limiter import _settings_limit, configure_rate_limiter

        sc = MagicMock()
        sc.get.side_effect = RuntimeError("db unavailable")
        configure_rate_limiter(sc)

        fn = _settings_limit("rate_limit_triage_per_ip", "20/minute")
        result = fn()

        assert result == "20/minute"

    def test_each_key_gets_independent_callable(self):
        from unittest.mock import MagicMock

        from poindexter.utils.rate_limiter import _settings_limit, configure_rate_limiter

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

        import poindexter.utils.rate_limiter as mod
        from poindexter.utils.rate_limiter import configure_rate_limiter

        sc = MagicMock()
        assert mod._site_config is None
        configure_rate_limiter(sc)
        assert mod._site_config is sc


# The backend package, anchored on this module's own file rather than a
# parents[N] depth (CLAUDE.md: the poindexter/ move made depth walks brittle).
_BACKEND_PKG = next(
    p for p in Path(__file__).resolve().parents
    if (p / "poindexter" / "utils" / "rate_limiter.py").is_file()
) / "poindexter"

# ~800 non-migration modules today. Far fewer means the scan lost its root.
_MIN_SCANNED = 500


@functools.cache
def _settings_limit_calls() -> tuple[tuple[tuple[str, str | None, str], ...], int]:
    """Every ``_settings_limit("<key>", "<default>")`` call in the backend.

    Returns ``(((key, inline_default, module), ...), modules_scanned)``, cached:
    one scan serves every test below. AST, not grep: the usage example in
    ``rate_limiter.py``'s docstring names a key without reading it, and must
    not count as a reader.
    """
    calls: list[tuple[str, str | None, str]] = []
    scanned = 0
    for path in sorted(_BACKEND_PKG.rglob("*.py")):
        if "migrations" in path.relative_to(_BACKEND_PKG).parts:
            continue
        scanned += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            key = node.args[0]
            if name != "_settings_limit" or not (
                isinstance(key, ast.Constant) and isinstance(key.value, str)
            ):
                continue
            default = node.args[1] if len(node.args) > 1 else None
            calls.append((
                key.value,
                default.value if isinstance(default, ast.Constant) else None,
                str(path.relative_to(_BACKEND_PKG.parent)),
            ))
    return tuple(calls), scanned


class TestSeededLimitsMatchReaders:
    """Seed and reader must agree, in both directions, for every rate limit.

    ``rate_limit_video_generate_per_ip`` stayed seeded in
    ``settings_defaults.DEFAULTS`` from 2026-07-10, when #2254 deleted its only
    reader (the limit on ``POST /api/video/generate/{post_id}``), until
    migration ``20260928_135025`` retired it. Nothing tied the seed to the
    decorator that read it, so the orphan was invisible. Both sets are derived
    from the tree here, so a deleted route whose key stays seeded fails the
    first test, and a new limited route whose key was never seeded fails the
    second.
    """

    def test_scan_saw_the_backend(self):
        calls, scanned = _settings_limit_calls()
        assert scanned >= _MIN_SCANNED, (
            f"scanned only {scanned} modules under {_BACKEND_PKG}; the scan root "
            "moved, so these contract tests are blind."
        )
        assert calls, "no _settings_limit(...) calls found; was the helper renamed?"

    def test_every_seeded_rate_limit_key_has_a_reader(self):
        from poindexter.services.settings_defaults import DEFAULTS

        read = {key for key, _, _ in _settings_limit_calls()[0]}
        seeded = sorted(k for k in DEFAULTS if k.startswith("rate_limit_"))
        for key in nonempty(seeded, "rate_limit_* keys in DEFAULTS"):
            assert key in read, (
                f"{key!r} is seeded in settings_defaults.DEFAULTS but no "
                "_settings_limit(...) call reads it, so no route is limited by "
                "it. If its route was deleted, retire the key: drop it from "
                "DEFAULTS and METADATA and add a DELETE migration (precedent: "
                "20260928_135025)."
            )

    def test_every_settings_limit_key_is_seeded(self):
        from poindexter.services.settings_defaults import DEFAULTS

        for key, _, module in nonempty(_settings_limit_calls()[0], "_settings_limit calls"):
            assert key in DEFAULTS, (
                f"{module} limits a route with _settings_limit({key!r}, ...), "
                "but settings_defaults.DEFAULTS does not seed it. Without a row, "
                "the limit silently runs on its inline default, with nothing for "
                "an operator to tune."
            )

    def test_inline_default_matches_the_seed(self):
        from poindexter.services.settings_defaults import DEFAULTS

        for key, default, module in nonempty(_settings_limit_calls()[0], "_settings_limit calls"):
            assert default == DEFAULTS.get(key), (
                f"{module}: _settings_limit({key!r}, {default!r}) disagrees with "
                f"the seeded default {DEFAULTS.get(key)!r}. The inline value is "
                "what runs whenever the row is missing or site_config is not "
                "wired yet."
            )
