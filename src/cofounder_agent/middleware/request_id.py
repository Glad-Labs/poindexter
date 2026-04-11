"""
Request ID Middleware

Generates a unique X-Request-ID for every HTTP request and propagates it:
1. Stored in a contextvars.ContextVar for the lifetime of the request.
2. Returned as an X-Request-ID response header.
3. Injected into every log record via a stdlib logging.Filter.

Usage:
    Any log line emitted during a request will automatically include the
    request_id field, enabling log correlation across all service layers:

        [request_id=abc-123] task_executor: starting pipeline
        [request_id=abc-123] content_agent: calling LLM
        [request_id=abc-123] database_service: writing result

    Callers can pass 'X-Request-ID' in their request headers to propagate
    an existing trace ID (e.g., from an API gateway or frontend).

Background task usage:
    Long-lived asyncio tasks (e.g., task_executor.py) run outside any HTTP
    request context. To enable log correlation for background processing, bind
    a synthetic trace ID for the lifetime of each unit of work:

        from middleware.request_id import _request_id_var

        token = _request_id_var.set(f"task-{task_id}")
        try:
            # all log lines inside here carry request_id=task-<uuid>
            await do_work()
        finally:
            _request_id_var.reset(token)
"""

import logging
import uuid
from contextvars import ContextVar
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Module-level ContextVar — survives concurrent async tasks within one request
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

HEADER_NAME = "X-Request-ID"


def get_request_id() -> str | None:
    """
    Return the current request ID, or None if called outside a request context.

    Call this from any service or utility module to obtain the ID without
    threading a parameter through every function signature.

    Example:
        from middleware.request_id import get_request_id
        logger.info(f"[{get_request_id()}] Starting heavy operation")
    """
    return _request_id_var.get()


class RequestIDFilter(logging.Filter):
    """
    Standard-library logging filter that injects request_id into every log record.

    Applied to the root logger so that ALL loggers (including those created
    with `logging.getLogger(__name__)`) automatically include the field.

    The filter is non-destructive: if no request is active, request_id is set
    to '-' so the field is always present and log parsers don't need to guard
    against its absence.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get() or "-"
        return True


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that assigns a request ID to every HTTP request.

    Behaviour:
    - If the incoming request carries an 'X-Request-ID' header, that value is
      reused (allows propagation from an upstream gateway or client).
    - Otherwise a new UUIDv4 is generated.
    - The ID is stored in _request_id_var for the duration of the request.
    - The ID is returned in the 'X-Request-ID' response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use caller-supplied ID or generate a fresh one
        incoming = request.headers.get(HEADER_NAME)
        request_id = incoming if incoming else str(uuid.uuid4())

        # Bind to async context — token needed to reset later
        token = _request_id_var.set(request_id)
        try:
            response: Response = await call_next(request)
        finally:
            # Reset the ContextVar when the request completes so it doesn't
            # leak into unrelated tasks that might reuse this worker.
            _request_id_var.reset(token)

        # Always return the ID in the response so clients can reference it
        response.headers[HEADER_NAME] = request_id
        return response
