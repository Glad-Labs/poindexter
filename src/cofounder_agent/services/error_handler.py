"""
Centralized Error Handling Infrastructure

Provides:
- AppError base exception class with error codes
- Centralized error response formatting
- Consistent HTTP status code mapping
- Structured logging integration
- Error context preservation

All routes should use these classes for consistent error responses.
"""

import logging
from typing import Dict, Any, Optional
from enum import Enum
from fastapi import HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "VALIDATION_ERROR",
                "message": "Invalid input parameter",
                "details": {"field": "topic", "issue": "must be at least 3 characters"},
                "request_id": "req-12345"
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
        **kwargs
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
        **kwargs
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
        **kwargs
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
