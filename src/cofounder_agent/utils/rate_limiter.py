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
"""

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address)
except ImportError:
    # Graceful no-op if slowapi is not installed.
    # Routes decorated with @limiter.limit() will still work but limiting
    # will be silently skipped.
    import logging

    logging.getLogger(__name__).warning(
        "slowapi not installed — rate limiting disabled. " "Install with: pip install slowapi"
    )

    class _NoOpLimiter:
        def limit(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    limiter = _NoOpLimiter()  # type: ignore[assignment]
