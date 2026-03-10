"""
Exception Handlers - Centralized error handling for FastAPI application

Provides structured error handling for:
- Application errors (AppError)
- Request validation errors (RequestValidationError)
- HTTP exceptions (HTTPException)
- Generic exceptions (fallback handler)

All handlers include:
- Request ID tracking for debugging
- Structured error responses
- Sentry integration for error tracking
- Proper logging with context
"""

import logging
import uuid
from typing import Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]
    SENTRY_AVAILABLE = False

from services.error_handler import AppError, NotFoundError, ValidationError, create_error_response

logger = logging.getLogger(__name__)


async def app_error_handler(request, exc: AppError):
    """
    Handle application-specific errors with structured response.

    Logs error with request context and includes request ID in response.
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    response = create_error_response(exc, request_id=request_id)

    # Log error with context
    logger.warning(
        f"AppError [{exc.error_code.value}]: {exc.message}",
        extra={"request_id": request_id, "details": exc.details},
    )

    return JSONResponse(
        status_code=exc.http_status_code,
        content=response.model_dump(exclude_none=True),
        headers={"X-Request-ID": request_id},
    )


async def validation_error_handler(request, exc: RequestValidationError):
    """
    Handle Pydantic request validation errors.

    Extracts field-level errors and returns them in structured format.
    All field errors are included in the response under the ``errors`` key
    so callers can surface field-specific validation feedback.
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

    # Extract all field-level errors (not just the first one)
    errors = {}
    for error in exc.errors():
        # loc[0] is "body"/"query"/"path" — skip it; the rest is the field path
        field_parts = error["loc"][1:]
        field = ".".join(str(x) for x in field_parts) if field_parts else "unknown"
        errors[field] = error["msg"]

    logger.warning(f"Request validation error: {errors}", extra={"request_id": request_id})

    response_body = {
        "error_code": "VALIDATION_ERROR",
        "message": "Request validation failed",
        "errors": errors,
        "request_id": request_id,
    }

    return JSONResponse(
        status_code=400,
        content=response_body,
        headers={"X-Request-ID": request_id},
    )


_STATUS_TO_ERROR_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "INVALID_STATE",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "SERVICE_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "TIMEOUT_ERROR",
}


async def http_exception_handler(request, exc: StarletteHTTPException):
    """
    Handle HTTPException from Starlette with structured response.

    Maps HTTP status codes to semantic error codes so callers receive
    consistent, machine-readable codes rather than the generic "HTTP_ERROR".
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    error_code = _STATUS_TO_ERROR_CODE.get(exc.status_code, "HTTP_ERROR")

    # If detail is already a structured dict (e.g. from AppError.to_http_exception()),
    # use it directly so the structured payload isn't double-wrapped.
    if isinstance(exc.detail, dict):
        response = {**exc.detail, "request_id": request_id}
    else:
        response = {
            "error_code": error_code,
            "message": exc.detail or "HTTP Error",
            "request_id": request_id,
        }

    logger.warning(f"HTTP Error {exc.status_code}: {exc.detail}", extra={"request_id": request_id})

    return JSONResponse(
        status_code=exc.status_code, content=response, headers={"X-Request-ID": request_id}
    )


async def generic_exception_handler(request, exc: Exception):
    """
    Handle all unhandled exceptions as fallback.

    Logs exception details, sends to Sentry if available,
    and returns generic error response.
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

    logger.error(
        f"Unhandled exception: {type(exc).__name__}", exc_info=exc, extra={"request_id": request_id}
    )

    # Send to Sentry if available
    if SENTRY_AVAILABLE and sentry_sdk is not None:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("request_id", request_id)
            scope.set_context(
                "request",
                {
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            sentry_sdk.capture_exception(exc)

    response = {
        "error_code": "INTERNAL_ERROR",
        "message": "Internal server error",
        "request_id": request_id,
    }

    return JSONResponse(status_code=500, content=response, headers={"X-Request-ID": request_id})


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register all exception handlers with the FastAPI application.

    Should be called during application startup, after app is created
    but before routes are registered.

    Args:
        app: FastAPI application instance

    Example:
        from utils.exception_handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(...)
    """
    # Register handlers in order of specificity (most specific first)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("✅ Exception handlers registered successfully")
