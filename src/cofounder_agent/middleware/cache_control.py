"""
HTTP Cache-Control Middleware

**Caching is opt-in.** Anything this middleware is not explicitly told to cache
comes back ``no-store``. A route that wants to be cached either sets its own
``Cache-Control`` (the middleware always defers to one already on the response)
or earns a prefix in the small allowlists below.

Why the default is deny
-----------------------
This used to be the other way round: unclassified paths fell through to a
catch-all ``private, max-age=60`` described as a "safe default". It was not one.
Two ways it went wrong, both measured on the live worker (2026-08-01):

1. The operator console polls ``/api/activity`` every 3s, ``/api/trace/…``
   every 6s, ``/api/gpu/queue`` and ``/api/logs`` every 10s. None matched a
   prefix, so all four got ``max-age=60`` and the browser answered most of
   those polls out of its own cache — a "live" panel showing minute-old data.
   An ``Authorization`` header does **not** stop a private cache from doing
   this; that was verified in a real browser, not inferred.
2. ``/health`` + ``/api/health`` matched the public list and got
   ``max-age=300`` while the console polls them every 30s. A fresh cache entry
   means the browser issues *no request at all*, so a worker that died would
   keep reading ``200 healthy`` in the console for up to five minutes. A
   liveness signal that cannot observe the thing it reports on is worse than
   none.

68 of 85 GET-able routes were reaching that catch-all, so the fallthrough was
the primary path rather than the exception, and every new route inherited
caching silently — the same failure that made the operator console serve a
mixed version of itself after a deploy (see ``utils/operator_console.py``).
Inverting it means a route someone forgot to classify is merely uncached, not
quietly wrong.

The API surface loses nothing by this. Its consumers — the console, the MCP
servers, the CLI — each drive their own request cadence, so HTTP caching never
reduced their load; it only decided how stale the answer was allowed to be.

What still opts in
------------------
- Static catalogs that change on deploy, not on operator action.
- File responses, which carry an ETag: ``no-cache`` revalidates cheaply
  (bodiless 304) instead of re-sending the bytes.

Vary
----
Every cacheable response also gets ``Vary: Authorization``. Without it a browser
keys its cache on URL alone, so two tokens hitting one URL inside the freshness
window can be served each other's body. With a single operator that is benign;
under the multi-tenant direction it is a cross-tenant leak, and it is far
cheaper to close now than to remember later.
"""

from collections.abc import Awaitable, Callable

from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from services.logger_config import get_logger

logger = get_logger(__name__)

NO_STORE = "no-store"
# "cache, but revalidate before every use" — not "don't cache" (that's
# no-store). Only useful where the response carries a validator; Starlette
# emits an ETag for FileResponse but not for JSONResponse, so a JSON route
# would pay a round trip that can never answer 304.
REVALIDATE = "no-cache"

# Paths that must never be cached regardless of method. Redundant with the
# deny-by-default now, but kept explicit: these are the ones where a future
# edit to the allowlists must not be able to make them cacheable by accident.
_NO_STORE_PREFIXES = (
    "/api/auth",
    "/api/oauth",
    "/api/token",
    "/ws",  # WebSocket upgrade requests
    "/dev/",  # Dev/debug endpoints
)

# Catalogs that change when the deploy changes, not when the operator acts.
#
# `/.well-known/*` is deliberately NOT here: the OAuth SDK sets its own
# `public, max-age=3600` on those responses, so the middleware defers and an
# entry here would be inert — and would misdescribe the live TTL besides.
_STATIC_PREFIXES = (
    "/api/capabilities",
    "/api/agent-registry",
    "/api/templates",
)
_STATIC_MAX_AGE = 300  # 5 minutes

# File responses (Starlette FileResponse ⇒ ETag + Last-Modified). Revalidating
# costs a bodiless 304 and can never serve a stale body.
_REVALIDATE_PREFIXES = ("/images/",)


def _cache_directive(path: str, method: str) -> str:
    """Return the Cache-Control directive for a request.

    Deny-by-default: every branch that is not an explicit opt-in returns
    ``no-store``.
    """
    # Only GET/HEAD are cacheable at all; everything else (mutations, and
    # anything exotic) is never stored.
    if method.upper() not in {"GET", "HEAD"}:
        return NO_STORE

    if any(path.startswith(prefix) for prefix in _NO_STORE_PREFIXES):
        return NO_STORE

    if any(path.startswith(prefix) for prefix in _STATIC_PREFIXES):
        return f"public, max-age={_STATIC_MAX_AGE}"

    if any(path.startswith(prefix) for prefix in _REVALIDATE_PREFIXES):
        return REVALIDATE

    return NO_STORE


def _is_storable(cache_control: str) -> bool:
    """True if a cache is allowed to keep this response at all.

    ``no-store`` is the only directive that forbids storage outright;
    ``no-cache`` still stores (it just revalidates first), so it needs ``Vary``
    every bit as much as ``public``/``private`` do.
    """
    return "no-store" not in cache_control.lower()


def _add_vary_authorization(headers: MutableHeaders) -> None:
    """Merge ``Authorization`` into any existing ``Vary``.

    Must merge rather than assign: ``CORSMiddleware`` is registered outside
    this one and appends ``Vary: Origin`` on the way out, and a route may have
    set its own. ``Vary: *`` already means "never reuse", so it is left alone.
    """
    existing = headers.get("vary")
    if not existing:
        headers["Vary"] = "Authorization"
        return

    parts = [p.strip() for p in existing.split(",") if p.strip()]
    if any(p == "*" or p.lower() == "authorization" for p in parts):
        return
    headers["Vary"] = ", ".join([*parts, "Authorization"])


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Stamps Cache-Control (deny-by-default) and Vary on every response.

    Does not override a ``Cache-Control`` a route set for itself, so an
    endpoint can opt into a stricter or more permissive policy than the prefix
    tables give it. ``utils/operator_console.py`` relies on exactly that to
    serve the console's unhashed static assets ``no-cache``.
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

        # Keyed off the EFFECTIVE policy, including one the route set itself —
        # a route opting into a long max-age is exactly where a missing Vary
        # would hurt most.
        if _is_storable(response.headers["cache-control"]):
            _add_vary_authorization(response.headers)

        return response
