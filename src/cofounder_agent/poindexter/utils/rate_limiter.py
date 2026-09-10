"""
Shared rate limiter instance for use in route decorators.

The limiter must be a module-level singleton so route files can reference
it at import/decoration time via @limiter.limit("N/minute").

The same instance is registered with app.state.limiter in middleware_config.py
so that slowapi's exception handler can find it at request time.

Usage in routes:
    from fastapi import Request
    from utils.rate_limiter import limiter

    @router.post("/my-endpoint")
    @limiter.limit("10/minute")
    async def my_endpoint(request: Request, body: MySchema = ..., ...):
        ...

Note: slowapi requires the raw starlette `Request` object as a parameter
in the route handler. Name it `request` (starlette.requests.Request).

Dynamic limits via _settings_limit():
    slowapi 0.1.9 calls dynamic-limit callables with ZERO arguments
    (unless the callable's single parameter is named "key", in which case
    it passes key_func(request) — the IP address string). There is no way
    to receive the Request object inside the callable. To bridge app_settings
    into the limit string, _settings_limit() captures a module-level
    _site_config reference that must be wired at app startup via
    configure_rate_limiter(site_config). When _site_config is None (tests
    that don't call configure_rate_limiter, or pre-startup), the callable
    falls back to the supplied default string.
"""

from typing import Any

# Module-level SiteConfig reference.  Wired by configure_rate_limiter() at
# app startup so _settings_limit callables can read live DB-backed values
# without the Request object (see module docstring for why).
_site_config: Any = None


def configure_rate_limiter(site_config: Any) -> None:
    """Wire the app's SiteConfig into the rate limiter.

    Called once from middleware_config._setup_rate_limiting so that
    _settings_limit callables can read live DB-backed values.
    Re-wiring on reload is a no-op — SiteConfig.reload() updates the same
    object in place, so the reference stays valid.
    """
    global _site_config
    _site_config = site_config


def _settings_limit(setting_key: str, default: str):
    """Return a zero-arg slowapi dynamic-limit callable that reads from app_settings.

    slowapi 0.1.9 calls the callable with NO arguments (see module docstring).
    The callable reads from the module-level _site_config reference wired by
    configure_rate_limiter().  Falls back to ``default`` when _site_config
    is unavailable (pre-startup or tests that omit configure_rate_limiter).

    Usage::

        @router.post("/token")
        @limiter.limit(_settings_limit("rate_limit_token_per_ip", "10/minute"))
        async def token(request: Request, ...):
            ...

    The callable is invoked per request; DB-reloaded settings take effect
    within the next reload cycle without redeploying.
    """
    def _limit() -> str:
        try:
            return _site_config.get(setting_key, default)
        except Exception:
            return default

    _limit.__name__ = f"_limit_{setting_key}"
    return _limit


try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address)
except ImportError:
    # Graceful no-op if slowapi is not installed.
    # Routes decorated with @limiter.limit() will still work but limiting
    # will be silently skipped.
    from services.logger_config import get_logger

    get_logger(__name__).warning(
        "slowapi not installed — rate limiting disabled. " "Install with: pip install slowapi"
    )

    class _NoOpLimiter:
        def limit(self, *args, **kwargs):  # noqa: ARG002 — signature compat with slowapi.Limiter
            def decorator(func):
                return func

            return decorator

    limiter = _NoOpLimiter()  # type: ignore[assignment]
