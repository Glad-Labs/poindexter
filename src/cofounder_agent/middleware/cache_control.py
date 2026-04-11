"""
HTTP Cache-Control Middleware

Sets appropriate Cache-Control headers on all API responses based on:
- HTTP method (mutations are never cached)
- Route category (public content vs. private user data vs. admin)

Strategy
--------
Mutations (POST/PUT/PATCH/DELETE):  no-store
Auth / WebSocket routes:             no-store
Private data  (/api/tasks, /api/workflows, /api/user, /api/approvals):
                                     private, max-age=60
Public content (/api/posts, /api/cms, /api/templates, /api/analytics):
                                     public, max-age=300
Everything else:                     private, max-age=60  (safe default)
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from services.logger_config import get_logger

logger = get_logger(__name__)
# Paths that must never be cached regardless of method
_NO_STORE_PREFIXES = (
    "/api/auth",
    "/api/oauth",
    "/api/token",
    "/ws",  # WebSocket upgrade requests
    "/dev/",  # Dev/debug endpoints
)

# Paths serving public, non-user-specific content
_PUBLIC_PREFIXES = (
    "/api/posts",
    "/api/cms",
    "/api/templates",
    "/api/analytics",
    "/api/agents/status",
    "/api/capabilities",
    "/api/agent-registry",
    "/health",
    "/api/health",
)

# Paths serving user/session-specific data
_PRIVATE_PREFIXES = (
    "/api/tasks",
    "/api/workflows",
    "/api/user",
    "/api/approvals",
    "/api/subtasks",
    "/api/bulk",
    "/api/settings",
    "/api/profiling",
    "/api/admin",
)

_PUBLIC_MAX_AGE = 300  # 5 minutes
_PRIVATE_MAX_AGE = 60  # 1 minute


def _cache_directive(path: str, method: str) -> str:
    """Return the appropriate Cache-Control directive for a request."""
    # Mutations and sensitive routes are never cached
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return "no-store"

    if any(path.startswith(prefix) for prefix in _NO_STORE_PREFIXES):
        return "no-store"

    if any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES):
        return f"public, max-age={_PUBLIC_MAX_AGE}"

    if any(path.startswith(prefix) for prefix in _PRIVATE_PREFIXES):
        return f"private, max-age={_PRIVATE_MAX_AGE}"

    # Safe default for anything unclassified
    return f"private, max-age={_PRIVATE_MAX_AGE}"


class CacheControlMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that stamps Cache-Control on every response.

    Does not override headers that a route handler has already set explicitly,
    so individual endpoints can opt into a stricter or more permissive policy.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)

        # Don't overwrite if the route already set its own Cache-Control
        if "cache-control" not in response.headers:
            directive = _cache_directive(request.url.path, request.method)
            response.headers["Cache-Control"] = directive

        return response
