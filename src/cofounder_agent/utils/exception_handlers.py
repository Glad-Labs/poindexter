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
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

from services.error_handler import AppError, ValidationError, NotFoundError, create_error_response

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
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

    # Extract field-level errors
    errors = {}
    for error in exc.errors():
        field = ".".join(str(x) for x in error["loc"][1:])
        errors[field] = error["msg"]

    response = create_error_response(
        ValidationError(
            "Request validation failed", field=list(errors.keys())[0] if errors else "unknown"
        ),
        request_id=request_id,
    )

    logger.warning(f"Request validation error: {errors}", extra={"request_id": request_id})

    return JSONResponse(
        status_code=400,
        content=response.model_dump(exclude_none=True),
        headers={"X-Request-ID": request_id},
    )


async def http_exception_handler(request, exc: StarletteHTTPException):
    """
    Handle HTTPException from Starlette with structured response.

    Converts HTTPException to standardized error format.
    """
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))

    response = {
        "error_code": "HTTP_ERROR",
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
    if SENTRY_AVAILABLE:
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
