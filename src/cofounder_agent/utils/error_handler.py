"""
Unified error handling utilities for API routes and services.

Provides consistent error handling patterns across the application,
reducing code duplication and improving maintainability.
"""

import logging
from typing import Optional, Any, Dict
from fastapi import HTTPException
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ErrorResponse:
    """Standardized error response format."""

    def __init__(self, status_code: int, detail: str, operation: str = None, error_type: str = None):
        """
        Initialize error response.

        Args:
            status_code: HTTP status code
            detail: Human-readable error detail
            operation: Operation that failed (for logging)
            error_type: Type of error for categorization
        """
        self.status_code = status_code
        self.detail = detail
        self.operation = operation
        self.error_type = error_type or "UnknownError"
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        return {
            "error": {
                "type": self.error_type,
                "detail": self.detail,
                "timestamp": self.timestamp,
            }
        }

    def to_http_exception(self) -> HTTPException:
        """Convert to FastAPI HTTPException."""
        return HTTPException(status_code=self.status_code, detail=self.detail)


async def handle_route_error(
    error: Exception,
    operation: str,
    logger_instance: logging.Logger = None,
    default_detail: str = None,
) -> HTTPException:
    """
    Unified error handler for API routes.

    Logs the error with context and returns appropriate HTTPException.

    Args:
        error: The exception that occurred
        operation: Name of operation for logging (e.g., "fetch_post", "create_task")
        logger_instance: Logger to use (defaults to module logger)
        default_detail: Default detail message if error has no detail

    Returns:
        HTTPException with appropriate status code and detail

    Raises:
        HTTPException: Always (after logging)

    Example:
        ```python
        try:
            post = await db.get_post(post_id)
        except HTTPException:
            raise
        except Exception as e:
            raise await handle_route_error(e, "get_post", logger)
        ```
    """
    log_instance = logger_instance or logger

    # Handle HTTPException (already formatted)
    if isinstance(error, HTTPException):
        return error

    # Determine error type and status code
    error_type = type(error).__name__
    status_code = 500  # Default to internal server error
    detail = default_detail or f"Error during {operation}"

    # Map common exceptions to status codes
    if isinstance(error, ValueError):
        status_code = 400
        error_type = "ValidationError"
        detail = str(error) if str(error) else f"Invalid input for {operation}"
    elif isinstance(error, KeyError):
        status_code = 400
        error_type = "MissingFieldError"
        detail = f"Missing required field: {str(error)}"
    elif isinstance(error, AttributeError):
        status_code = 400
        error_type = "InvalidAttributeError"
        detail = f"Invalid attribute: {str(error)}"
    elif isinstance(error, TimeoutError):
        status_code = 504
        error_type = "TimeoutError"
        detail = f"Operation {operation} timed out"
    elif isinstance(error, ConnectionError):
        status_code = 503
        error_type = "ServiceUnavailableError"
        detail = f"Service unavailable during {operation}"
    elif isinstance(error, PermissionError):
        status_code = 403
        error_type = "PermissionDeniedError"
        detail = "Permission denied"

    # Log with appropriate level
    if status_code >= 500:
        log_instance.error(
            f"[{operation}] {error_type}: {str(error)}",
            exc_info=True,
            extra={"operation": operation, "error_type": error_type},
        )
    else:
        log_instance.warning(
            f"[{operation}] {error_type}: {str(error)}",
            extra={"operation": operation, "error_type": error_type},
        )

    return HTTPException(status_code=status_code, detail=detail)


def handle_service_error(
    error: Exception,
    operation: str,
    logger_instance: logging.Logger = None,
    fallback_value: Any = None,
) -> Any:
    """
    Unified error handler for service methods.

    Logs the error and optionally returns a fallback value instead of raising.

    Args:
        error: The exception that occurred
        operation: Name of operation for logging
        logger_instance: Logger to use
        fallback_value: Value to return on error (if None, error is re-raised)

    Returns:
        fallback_value if provided, otherwise raises HTTPException

    Example:
        ```python
        try:
            users = await db.get_users()
        except Exception as e:
            return handle_service_error(e, "get_users", logger, fallback_value=[])
        ```
    """
    log_instance = logger_instance or logger
    error_type = type(error).__name__

    log_instance.error(
        f"[SERVICE:{operation}] {error_type}: {str(error)}",
        exc_info=True,
        extra={"operation": operation, "service": "backend", "error_type": error_type},
    )

    if fallback_value is not None:
        log_instance.info(f"[SERVICE:{operation}] Returning fallback value: {type(fallback_value).__name__}")
        return fallback_value

    # If no fallback, re-raise as HTTPException
    raise HTTPException(status_code=500, detail=f"Error during {operation}: {str(error)}")


def create_error_response(
    error: Exception,
    operation: str = "operation",
    status_code: int = 500,
) -> Dict[str, Any]:
    """
    Create a standardized error response dictionary.

    Args:
        error: The exception that occurred
        operation: Name of operation
        status_code: HTTP status code

    Returns:
        Dictionary with standardized error format

    Example:
        ```python
        error_resp = create_error_response(e, "create_post", 400)
        # {
        #     "error": {
        #         "type": "ValueError",
        #         "detail": "Invalid input",
        #         "timestamp": "2026-01-17T12:34:56+00:00"
        #     }
        # }
        ```
    """
    error_response = ErrorResponse(
        status_code=status_code,
        detail=str(error) or "An error occurred",
        operation=operation,
        error_type=type(error).__name__,
    )
    return error_response.to_dict()


def log_and_raise_http_error(
    status_code: int,
    detail: str,
    operation: str = None,
    logger_instance: logging.Logger = None,
) -> None:
    """
    Log an error and raise HTTPException.

    Useful for validation errors and other non-exception errors.

    Args:
        status_code: HTTP status code
        detail: Error detail message
        operation: Operation name for logging
        logger_instance: Logger to use

    Raises:
        HTTPException: Always

    Example:
        ```python
        if not post:
            log_and_raise_http_error(404, "Post not found", "get_post", logger)
        ```
    """
    log_instance = logger_instance or logger

    if status_code >= 500:
        log_instance.error(f"[{operation}] HTTP {status_code}: {detail}")
    else:
        log_instance.warning(f"[{operation}] HTTP {status_code}: {detail}")

    raise HTTPException(status_code=status_code, detail=detail)


# Convenience functions for common errors
def not_found(detail: str = "Resource not found", operation: str = None) -> HTTPException:
    """Raise 404 Not Found error."""
    if operation:
        logger.warning(f"[{operation}] Resource not found: {detail}")
    return HTTPException(status_code=404, detail=detail)


def bad_request(detail: str = "Invalid request", operation: str = None) -> HTTPException:
    """Raise 400 Bad Request error."""
    if operation:
        logger.warning(f"[{operation}] Bad request: {detail}")
    return HTTPException(status_code=400, detail=detail)


def forbidden(detail: str = "Access denied", operation: str = None) -> HTTPException:
    """Raise 403 Forbidden error."""
    if operation:
        logger.warning(f"[{operation}] Forbidden: {detail}")
    return HTTPException(status_code=403, detail=detail)


def internal_error(detail: str = "Internal server error", operation: str = None) -> HTTPException:
    """Raise 500 Internal Server Error."""
    if operation:
        logger.error(f"[{operation}] Internal error: {detail}")
    return HTTPException(status_code=500, detail=detail)


def service_unavailable(detail: str = "Service unavailable", operation: str = None) -> HTTPException:
    """Raise 503 Service Unavailable error."""
    if operation:
        logger.error(f"[{operation}] Service unavailable: {detail}")
    return HTTPException(status_code=503, detail=detail)
