"""
Domain Error Classes and Error Handling

Provides domain exception classes and error codes:
- AppError base class + subclasses: ValidationError, NotFoundError, DatabaseError, etc.
- ErrorCode enum for classification
- handle_error() for converting exceptions to AppError
- create_error_response() for standardized API error responses

Import guide:
    - Routes: from services.error_handler import AppError, NotFoundError
    - Services (same package): from .error_handler import DatabaseError, ServiceError
    - For route/service error handling helpers (handle_route_error, handle_service_error):
      from utils.error_handler import handle_route_error, handle_service_error
"""

from enum import Enum
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict

from services.logger_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# ERROR CODES & ENUMS
# ============================================================================


class ErrorCode(str, Enum):
    """Standard error codes for API responses"""

    # Validation errors (400)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    CONSTRAINT_VIOLATION = "CONSTRAINT_VIOLATION"

    # Authentication/Authorization errors (401/403)
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    TOKEN_INVALID = "TOKEN_INVALID"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # Not found errors (404)
    NOT_FOUND = "NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    USER_NOT_FOUND = "USER_NOT_FOUND"

    # Conflict/State errors (409/422)
    CONFLICT = "CONFLICT"
    STATE_ERROR = "STATE_ERROR"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    INVALID_STATE = "INVALID_STATE"

    # Server errors (500)
    INTERNAL_ERROR = "INTERNAL_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"

    # Operation errors (202/503)
    OPERATION_FAILED = "OPERATION_FAILED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"


# ============================================================================
# ERROR RESPONSE MODELS
# ============================================================================


class ErrorResponse(BaseModel):
    """Standard error response format"""

    error_code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error_code": "VALIDATION_ERROR",
                "message": "Invalid input parameter",
                "details": {"field": "topic", "issue": "must be at least 3 characters"},
                "request_id": "req-12345",
            }
        }
    )


# ============================================================================
# APP ERROR BASE CLASS
# ============================================================================


class AppError(Exception):
    """
    Base application error with standard error handling.

    All domain-specific errors should inherit from this.

    Features:
    - Error codes for classification
    - HTTP status code mapping
    - Structured error details
    - Automatic logging
    - Request context preservation
    """

    # Default error code and status - override in subclasses
    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        error_code: ErrorCode | None = None,
        http_status_code: int | None = None,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
        request_id: str | None = None,
    ):
        """
        Initialize application error.

        Args:
            message: Human-readable error message
            error_code: ErrorCode enum value (defaults to class default)
            http_status_code: HTTP status code (defaults to class default)
            details: Additional error details/context
            cause: Original exception (for error chain)
            request_id: Optional request ID for tracing
        """
        self.message = message
        self.error_code = error_code or self.__class__.error_code
        self.http_status_code = http_status_code or self.__class__.http_status_code
        self.details = details or {}
        self.cause = cause
        self.request_id = request_id

        super().__init__(message)

    def to_response(self) -> ErrorResponse:
        """Convert error to standard response format"""
        return ErrorResponse(
            error_code=self.error_code.value,
            message=self.message,
            details=self.details if self.details else None,
            request_id=self.request_id,
        )

    def to_http_exception(self) -> HTTPException:
        """Convert to FastAPI HTTPException for direct raising"""
        return HTTPException(
            status_code=self.http_status_code,
            detail=self.to_response().model_dump(exclude_none=True),
        )

    def __str__(self) -> str:
        """String representation with context"""
        msg = f"[{self.error_code.value}] {self.message}"
        if self.details:
            msg += f" | Details: {self.details}"
        return msg


# ============================================================================
# DOMAIN-SPECIFIC ERROR CLASSES
# ============================================================================


class ValidationError(AppError):
    """Input validation errors (400)"""

    error_code = ErrorCode.VALIDATION_ERROR
    http_status_code = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: Any | None = None,
        constraint: str | None = None,
        **kwargs,
    ):
        details = {"field": field, "constraint": constraint}
        if value is not None:
            details["value"] = str(value)

        details = {k: v for k, v in details.items() if v is not None}

        super().__init__(message, details=details, **kwargs)


class NotFoundError(AppError):
    """Resource not found errors (404)"""

    error_code = ErrorCode.NOT_FOUND
    http_status_code = status.HTTP_404_NOT_FOUND

    def __init__(
        self,
        message: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        **kwargs,
    ):
        details = {}
        if resource_type:
            details["resource_type"] = resource_type
        if resource_id:
            details["resource_id"] = str(resource_id)

        super().__init__(message, details=details, **kwargs)


class UnauthorizedError(AppError):
    """Authentication errors (401)"""

    error_code = ErrorCode.UNAUTHORIZED
    http_status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    """Authorization errors (403)"""

    error_code = ErrorCode.FORBIDDEN
    http_status_code = status.HTTP_403_FORBIDDEN


class ConflictError(AppError):
    """Resource conflict errors (409)"""

    error_code = ErrorCode.CONFLICT
    http_status_code = status.HTTP_409_CONFLICT


class DatabaseError(AppError):
    """Database operation errors (500)"""

    error_code = ErrorCode.DATABASE_ERROR
    http_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class ServiceError(AppError):
    """Service operation errors (500)"""

    error_code = ErrorCode.SERVICE_ERROR
    http_status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


class TimeoutError(AppError):
    """Operation timeout errors (504)"""

    error_code = ErrorCode.TIMEOUT_ERROR
    http_status_code = status.HTTP_504_GATEWAY_TIMEOUT


# ============================================================================
# ERROR HANDLING UTILITIES
# ============================================================================


def handle_error(
    error: Exception,
    default_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
    log_exception: bool = True,
    context: dict[str, Any] | None = None,
) -> AppError:
    """
    Convert any exception to AppError for consistent handling.

    Args:
        error: Exception to handle
        default_code: Default error code if not recognized
        log_exception: Whether to log exception details
        context: Additional context information

    Returns:
        AppError instance (or converted error if already AppError)
    """
    # If already an AppError, return as-is
    if isinstance(error, AppError):
        return error

    # Log the error if requested
    if log_exception:
        logger.error("Unhandled exception: %s", error, exc_info=True)

    # Convert to ServiceError — do not expose raw exception message in HTTP response
    details = context or {}
    details["error_type"] = type(error).__name__

    return ServiceError(
        message="An internal service error occurred",
        error_code=default_code,
        details=details,
        cause=error,
    )


def create_error_response(
    error: Exception,
    request_id: str | None = None,
) -> ErrorResponse:
    """
    Create standardized error response from any exception.

    Args:
        error: Exception to convert
        request_id: Optional request ID for tracing

    Returns:
        ErrorResponse ready for HTTP response
    """
    if isinstance(error, AppError):
        error.request_id = request_id
        return error.to_response()

    # Convert unknown exception
    app_error = handle_error(error)
    app_error.request_id = request_id
    return app_error.to_response()
