"""
Prometheus HTTP RED-metrics middleware.

Records Rate / Errors / Duration for every HTTP request the worker serves:

- ``poindexter_http_requests_total{method,route,status}`` — request rate and
  (via ``status``) error rate.
- ``poindexter_http_request_duration_seconds{method,route}`` — latency
  histogram (p50/p95/p99 via ``histogram_quantile``).

The worker is a FastAPI app but previously exposed zero request-level
observability — you could see Postgres query latency but not the HTTP latency
clients actually experience. This closes that gap.

## Why a pure ASGI middleware (not BaseHTTPMiddleware)

The ``route`` label must be the matched route TEMPLATE (``/api/posts/{slug}``),
which is derived from ``scope['endpoint']`` / ``scope['path_params']``. Those
keys are set by Starlette's router via ``scope.update(child_scope)`` — an
in-place mutation of the same dict this middleware passes down. A pure ASGI
middleware holds a reference to that dict and reads the populated values after
``self.app(...)`` returns. ``BaseHTTPMiddleware`` runs the downstream app in a
separate task and does not reliably surface those scope mutations, so it can't
see the matched route. Pure ASGI is also cheaper (no per-request task group).

The status code is captured from the ``http.response.start`` ASGI event. If the
inner app raises before sending a response, the ``finally`` block still records
the request with status ``500`` so error rate stays honest.

See ``services.metrics_exporter`` for the metric definitions and the
``http_route_label()`` cardinality-control helper.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.metrics_exporter import (
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    http_route_label,
)


class PrometheusMetricsMiddleware:
    """ASGI middleware that records HTTP RED metrics for each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only instrument HTTP requests — websockets/lifespan pass straight
        # through (they have no status code / route template in this sense).
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        start = time.perf_counter()
        # Default to 500: if the app errors before emitting a response.start,
        # the request still counts as a server error rather than vanishing.
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            # Read the route template AFTER the app ran — the router has by
            # now mutated `scope` in place with endpoint + path_params.
            route = http_route_label(scope)
            _observe(method, route, status_code, duration)


def _observe(method: str, route: str, status_code: Any, duration: float) -> None:
    """Record one request into both metrics, never raising into the response.

    Metric recording must not be able to break a request that already
    succeeded, so any labeling/observe error is swallowed (the request is
    already done by the time `finally` runs).
    """
    try:
        HTTP_REQUEST_DURATION_SECONDS.labels(method=method, route=route).observe(
            duration
        )
        HTTP_REQUESTS_TOTAL.labels(
            method=method, route=route, status=str(status_code)
        ).inc()
    except Exception:  # pragma: no cover - defensive, metrics must never 500 a request
        pass
