"""
Comprehensive Error Handling and Recovery System

Provides:
- StandardError codes and error classification
- AppError base exception class with full context
- Domain-specific exception classes for all error scenarios
- Retry logic with exponential backoff for resilience
- Circuit breaker pattern for external service protection
- Graceful error handling with structured context
- Request error tracking and correlation
- Database connection recovery
- Timeout management
- Validation utilities with field-level error reporting
- Standardized error response formatting

All routes should use AppError classes for consistent error responses.
Integrate retry_with_backoff and CircuitBreaker for resilience.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Coroutine, Dict, Optional, TypeVar

from fastapi import HTTPException, status
from pydantic import BaseModel

try:
    import sentry_sdk

    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False

logger = logging.getLogger(__name__)
T = TypeVar("T")


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


class ErrorCategory(str, Enum):
    """Error categories for tracking and recovery"""

    DATABASE = "database"
    NETWORK = "network"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    RATE_LIMIT = "rate_limit"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL = "internal"
    EXTERNAL_SERVICE = "external_service"


# ============================================================================
# ERROR CONTEXT
# ============================================================================


@dataclass
class ErrorContext:
    """Context information for error tracking and recovery"""

    category: ErrorCategory
    service: str
    operation: str
    attempt: int = 1
    max_attempts: int = 3
    request_id: Optional[str] = None
    user_id: Optional[str] = None
    timestamp: datetime = None
    error: Optional[Exception] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
        if self.metadata is None:
            self.metadata = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for logging/tracking"""
        return {
            "category": self.category.value,
            "service": self.service,
            "operation": self.operation,
            "attempt": self.attempt,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "error_type": type(self.error).__name__ if self.error else None,
            "error_message": str(self.error) if self.error else None,
            **self.metadata,
        }


# ============================================================================
# ERROR RESPONSE MODELS
# ============================================================================


class ErrorResponse(BaseModel):
    """Standard error response format"""

    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "VALIDATION_ERROR",
                "message": "Invalid input parameter",
                "details": {"field": "topic", "issue": "must be at least 3 characters"},
                "request_id": "req-12345",
            }
        }


class ErrorDetail(BaseModel):
    """Detailed error information for complex errors"""

    code: str
    field: Optional[str] = None
    value: Optional[Any] = None
    constraint: Optional[str] = None


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
        error_code: Optional[ErrorCode] = None,
        http_status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        request_id: Optional[str] = None,
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
        field: Optional[str] = None,
        value: Optional[Any] = None,
        constraint: Optional[str] = None,
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
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
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


class StateError(AppError):
    """Invalid state transition errors (422)"""

    error_code = ErrorCode.INVALID_STATE
    http_status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    def __init__(
        self,
        message: str,
        current_state: Optional[str] = None,
        requested_action: Optional[str] = None,
        **kwargs,
    ):
        details = {}
        if current_state:
            details["current_state"] = current_state
        if requested_action:
            details["requested_action"] = requested_action

        super().__init__(message, details=details, **kwargs)


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
    context: Optional[Dict[str, Any]] = None,
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
        logger.error(f"Unhandled exception: {error}", exc_info=True)

    # Convert to ServiceError
    details = context or {}
    details["original_error"] = str(error)

    return ServiceError(
        message=f"Service error: {str(error)}",
        error_code=default_code,
        details=details,
        cause=error,
    )


class CircuitBreaker:
    """
    Circuit breaker pattern implementation for external service calls.

    Prevents cascading failures by:
    - Failing fast when service is down
    - Exponential backoff for recovery attempts
    - Tracking failure rates
    - Resetting on successful calls

    Usage:
        breaker = CircuitBreaker("ollama", failure_threshold=5, recovery_timeout=60)
        result = await breaker.call_async(external_api_call)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False
        self.last_state_change = datetime.utcnow()

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            HTTPException: If circuit is open (service unavailable)
        """
        # Check if circuit should be reset (recovery time passed)
        if self.is_open and self._should_attempt_reset():
            self.is_open = False
            self.failure_count = 0
            logger.info(f"🔄 Circuit breaker '{self.name}' attempting reset")

        # If circuit is open, fail fast
        if self.is_open:
            logger.warning(f"⚠️  Circuit breaker '{self.name}' is OPEN - failing fast")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Service '{self.name}' temporarily unavailable. Retry in {self.recovery_timeout}s.",
            )

        # Execute function
        try:
            result = func(*args, **kwargs)

            # Success - reset failure count
            if self.failure_count > 0:
                logger.info(f"✅ Circuit breaker '{self.name}' - call successful, resetting")
            self.failure_count = 0
            self.last_failure_time = None

            return result

        except self.expected_exception as e:
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()

            logger.warning(
                f"⚠️  Circuit breaker '{self.name}' - failure {self.failure_count}/{self.failure_threshold}: {e}"
            )

            # Open circuit if threshold reached
            if self.failure_count >= self.failure_threshold:
                self.is_open = True
                self.last_state_change = datetime.utcnow()
                logger.error(f"🔴 Circuit breaker '{self.name}' is now OPEN")

                # Report to Sentry if available
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)

            raise

    async def call_async(self, func: Callable[..., Coroutine[Any, Any, T]], *args, **kwargs) -> T:
        """Async version of call method."""
        # Check if circuit should be reset
        if self.is_open and self._should_attempt_reset():
            self.is_open = False
            self.failure_count = 0
            logger.info(f"🔄 Circuit breaker '{self.name}' attempting reset")

        # If circuit is open, fail fast
        if self.is_open:
            logger.warning(f"⚠️  Circuit breaker '{self.name}' is OPEN - failing fast")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Service '{self.name}' temporarily unavailable. Retry in {self.recovery_timeout}s.",
            )

        # Execute async function
        try:
            result = await func(*args, **kwargs)

            # Success - reset failure count
            if self.failure_count > 0:
                logger.info(f"✅ Circuit breaker '{self.name}' - call successful, resetting")
            self.failure_count = 0
            self.last_failure_time = None

            return result

        except self.expected_exception as e:
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()

            logger.warning(
                f"⚠️  Circuit breaker '{self.name}' - failure {self.failure_count}/{self.failure_threshold}: {e}"
            )

            # Open circuit if threshold reached
            if self.failure_count >= self.failure_threshold:
                self.is_open = True
                self.last_state_change = datetime.utcnow()
                logger.error(f"🔴 Circuit breaker '{self.name}' is now OPEN")

                # Report to Sentry if available
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)

            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery"""
        if not self.last_failure_time:
            return False
        return (datetime.utcnow() - self.last_failure_time).total_seconds() >= self.recovery_timeout

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring"""
        return {
            "name": self.name,
            "state": "open" if self.is_open else "closed",
            "failure_count": self.failure_count,
            "threshold": self.failure_threshold,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_state_change": self.last_state_change.isoformat(),
        }


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    on_exception: type = Exception,
    on_error_callback: Optional[Callable] = None,
):
    """
    Decorator for retry logic with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff calculation
        on_exception: Exception type to catch and retry on
        on_error_callback: Optional callback on error (for logging, etc.)

    Example:
        @retry_with_backoff(max_retries=3, initial_delay=1.0)
        async def call_external_api():
            return await external_api.fetch_data()
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except on_exception as e:
                    last_exception = e

                    if attempt >= max_retries:
                        # Final attempt failed
                        logger.error(
                            f"❌ {func.__name__} failed after {max_retries + 1} attempts: {e}",
                            exc_info=True,
                        )
                        if on_error_callback:
                            on_error_callback(e, attempt + 1, max_retries + 1)
                        raise

                    # Wait before retry with exponential backoff
                    logger.warning(
                        f"⚠️  {func.__name__} attempt {attempt + 1} failed, retrying in {delay}s: {e}"
                    )

                    if on_error_callback:
                        on_error_callback(e, attempt + 1, max_retries + 1)

                    await asyncio.sleep(delay)
                    delay = min(delay * exponential_base, max_delay)

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except on_exception as e:
                    if attempt >= max_retries:
                        logger.error(
                            f"❌ {func.__name__} failed after {max_retries + 1} attempts: {e}",
                            exc_info=True,
                        )
                        if on_error_callback:
                            on_error_callback(e, attempt + 1, max_retries + 1)
                        raise

                    logger.warning(
                        f"⚠️  {func.__name__} attempt {attempt + 1} failed, retrying in {delay}s: {e}"
                    )

                    if on_error_callback:
                        on_error_callback(e, attempt + 1, max_retries + 1)

                    time.sleep(delay)
                    delay = min(delay * exponential_base, max_delay)

        # Return async or sync wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class ErrorResponseFormatter:
    """Standardized error response formatting"""

    @staticmethod
    def format_error(
        error: Exception,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        """
        Format error as standardized API response.

        Args:
            error: Exception instance
            request_id: Request correlation ID
            user_id: Affected user ID
            include_details: Include detailed error information (only in dev)

        Returns:
            Standardized error response dictionary
        """
        error_dict = {
            "error": type(error).__name__,
            "message": str(error),
            "timestamp": datetime.utcnow().isoformat(),
        }

        if request_id:
            error_dict["request_id"] = request_id

        if user_id:
            error_dict["user_id"] = user_id

        if include_details:
            import traceback

            error_dict["traceback"] = traceback.format_exc()

        return error_dict


def log_error_context(context: ErrorContext) -> None:
    """
    Log error with full context for debugging.

    Args:
        context: ErrorContext with error details
    """
    context_dict = context.to_dict()

    log_message = (
        f"[{context.category.value.upper()}] "
        f"Service: {context.service} | "
        f"Operation: {context.operation} | "
        f"Attempt: {context.attempt}/{context.max_attempts}"
    )

    logger.error(log_message, extra=context_dict, exc_info=context.error)

    # Send to Sentry if available
    if SENTRY_AVAILABLE and context.error:
        with sentry_sdk.push_scope() as scope:
            scope.set_context("error_context", context_dict)
            if context.request_id:
                scope.set_tag("request_id", context.request_id)
            if context.user_id:
                scope.set_user({"id": context.user_id})
            sentry_sdk.capture_exception(context.error)


def create_error_response(
    error: Exception,
    request_id: Optional[str] = None,
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


# ============================================================================
# VALIDATION HELPERS
# ============================================================================


def validate_string_field(
    value: str,
    field_name: str,
    min_length: int = 1,
    max_length: Optional[int] = None,
) -> str:
    """
    Validate string field with common checks.

    Args:
        value: Value to validate
        field_name: Field name for error reporting
        min_length: Minimum length (default 1)
        max_length: Maximum length (optional)

    Returns:
        Validated value (trimmed)

    Raises:
        ValidationError if validation fails
    """
    if not value or not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string",
            field=field_name,
        )

    value = value.strip()

    if len(value) < min_length:
        raise ValidationError(
            f"{field_name} must be at least {min_length} character(s)",
            field=field_name,
            constraint=f"min_length={min_length}",
        )

    if max_length and len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters",
            field=field_name,
            constraint=f"max_length={max_length}",
        )

    return value


def validate_integer_field(
    value: int,
    field_name: str,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
) -> int:
    """
    Validate integer field with range checks.

    Args:
        value: Value to validate
        field_name: Field name for error reporting
        min_value: Minimum value (optional)
        max_value: Maximum value (optional)

    Returns:
        Validated value

    Raises:
        ValidationError if validation fails
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be an integer",
            field=field_name,
        )

    if min_value is not None and value < min_value:
        raise ValidationError(
            f"{field_name} must be at least {min_value}",
            field=field_name,
            constraint=f"min={min_value}",
        )

    if max_value is not None and value > max_value:
        raise ValidationError(
            f"{field_name} cannot exceed {max_value}",
            field=field_name,
            constraint=f"max={max_value}",
        )

    return value


def validate_enum_field(
    value: str,
    field_name: str,
    enum_class,
    case_insensitive: bool = False,
) -> str:
    """
    Validate enum field.

    Args:
        value: Value to validate
        field_name: Field name for error reporting
        enum_class: Enum class to validate against
        case_insensitive: Whether to allow case-insensitive matching

    Returns:
        Validated enum value (as string)

    Raises:
        ValidationError if validation fails
    """
    valid_values = [e.value for e in enum_class]

    if case_insensitive:
        value_lower = value.lower()
        for enum_val in enum_class:
            if enum_val.value.lower() == value_lower:
                return enum_val.value

    if value not in valid_values:
        raise ValidationError(
            f"{field_name} must be one of: {', '.join(valid_values)}",
            field=field_name,
            constraint=f"enum={enum_class.__name__}",
        )

    return value
