"""
Error Response Builder - Standardized error responses across routes

This module provides a centralized way to create consistent error responses
throughout the application, eliminating ad-hoc error handling and response
formatting across multiple route files.

Provides:
- ErrorResponse builder with fluent API
- Standardized response formats
- Common error response factory methods
- Request ID tracking
- Consistent HTTP status codes

Eliminates:
- Scattered error response creation
- Inconsistent response formats
- Duplicate error handling code
- Unclear error semantics
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# RESPONSE MODELS
# ============================================================================


class ErrorDetail(BaseModel):
    """Individual error detail with field and message"""

    field: Optional[str] = Field(None, description="Field name if applicable")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code for client handling")


class ErrorResponse(BaseModel):
    """Standard error response model"""

    status: str = Field("error", description="Response status")
    error_code: str = Field(..., description="Standardized error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[List[ErrorDetail]] = Field(None, description="Detailed error information")
    request_id: Optional[str] = Field(None, description="Request ID for tracing")
    timestamp: Optional[str] = Field(None, description="ISO 8601 timestamp")
    path: Optional[str] = Field(None, description="Request path")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "error",
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": [
                    {"field": "task_name", "message": "Field required", "code": "REQUIRED"}
                ],
                "request_id": "req-12345678",
                "timestamp": "2024-12-08T10:30:00Z",
                "path": "/api/v1/tasks",
            }
        }


class SuccessResponse(BaseModel):
    """Standard success response model"""

    status: str = Field("success", description="Response status")
    data: Any = Field(..., description="Response data")
    request_id: Optional[str] = Field(None, description="Request ID for tracing")
    timestamp: Optional[str] = Field(None, description="ISO 8601 timestamp")


# ============================================================================
# ERROR RESPONSE BUILDER
# ============================================================================


class ErrorResponseBuilder:
    """
    Fluent builder for creating consistent error responses.

    Usage:
        response = (ErrorResponseBuilder()
            .error_code("VALIDATION_ERROR")
            .message("Request validation failed")
            .with_field_error("task_name", "Field required", "REQUIRED")
            .with_field_error("topic", "Field required", "REQUIRED")
            .request_id(request_id)
            .path(request.url.path)
            .build())

    Or use factory methods:
        response = ErrorResponseBuilder.validation_error(
            "Request validation failed",
            details=[("task_name", "Field required")]
        ).request_id(request_id).build()
    """

    def __init__(self):
        """Initialize builder with empty error response"""
        self._error_code: Optional[str] = None
        self._message: Optional[str] = None
        self._details: List[ErrorDetail] = []
        self._request_id: Optional[str] = None
        self._path: Optional[str] = None
        self._timestamp: Optional[str] = None

    def error_code(self, code: str) -> "ErrorResponseBuilder":
        """Set the error code"""
        self._error_code = code
        return self

    def message(self, msg: str) -> "ErrorResponseBuilder":
        """Set the error message"""
        self._message = msg
        return self

    def with_detail(
        self, message: str, field: Optional[str] = None, code: Optional[str] = None
    ) -> "ErrorResponseBuilder":
        """Add a detail to the error"""
        detail = ErrorDetail(field=field, message=message, code=code)
        self._details.append(detail)
        return self

    def with_field_error(
        self, field: str, message: str, code: Optional[str] = None
    ) -> "ErrorResponseBuilder":
        """Add a field-specific error"""
        detail = ErrorDetail(field=field, message=message, code=code)
        self._details.append(detail)
        return self

    def with_details(self, details: List[Dict[str, str]]) -> "ErrorResponseBuilder":
        """Add multiple details"""
        for detail in details:
            self.with_detail(
                message=detail.get("message", ""),
                field=detail.get("field"),
                code=detail.get("code"),
            )
        return self

    def request_id(self, request_id: Optional[str]) -> "ErrorResponseBuilder":
        """Set the request ID for tracing"""
        self._request_id = request_id
        return self

    def path(self, path: Optional[str]) -> "ErrorResponseBuilder":
        """Set the request path"""
        self._path = path
        return self

    def timestamp(self) -> "ErrorResponseBuilder":
        """Add current timestamp"""
        self._timestamp = datetime.utcnow().isoformat() + "Z"
        return self

    def build(self) -> ErrorResponse:
        """Build the final error response"""
        if not self._error_code:
            raise ValueError("error_code is required")
        if not self._message:
            raise ValueError("message is required")

        return ErrorResponse(
            status="error",
            error_code=self._error_code,
            message=self._message,
            details=self._details if self._details else None,
            request_id=self._request_id,
            path=self._path,
            timestamp=self._timestamp,
        )

    def build_dict(self) -> Dict[str, Any]:
        """Build as dictionary (useful for JSONResponse)"""
        return self.build().model_dump(exclude_none=True)

    @staticmethod
    def validation_error(
        message: str = "Request validation failed", details: Optional[List[tuple]] = None
    ) -> "ErrorResponseBuilder":
        """
        Factory method for validation errors.

        Args:
            message: Error message
            details: List of (field, error_message) tuples

        Returns:
            ErrorResponseBuilder instance

        Example:
            builder = ErrorResponseBuilder.validation_error(
                details=[
                    ("task_name", "Field required"),
                    ("topic", "Field required")
                ]
            )
        """
        builder = ErrorResponseBuilder()
        builder.error_code("VALIDATION_ERROR")
        builder.message(message)

        if details:
            for field, error_msg in details:
                builder.with_field_error(field, error_msg, "VALIDATION_ERROR")

        return builder

    @staticmethod
    def not_found(
        resource_type: str, resource_id: Optional[str] = None, message: Optional[str] = None
    ) -> "ErrorResponseBuilder":
        """
        Factory method for 404 Not Found errors.

        Args:
            resource_type: Type of resource (e.g., "task", "user")
            resource_id: ID of the resource (optional)
            message: Custom message (optional)

        Returns:
            ErrorResponseBuilder instance

        Example:
            builder = ErrorResponseBuilder.not_found("task", task_id="12345")
        """
        builder = ErrorResponseBuilder()
        builder.error_code("NOT_FOUND")

        if message:
            builder.message(message)
        elif resource_id:
            builder.message(f"{resource_type.title()} with ID '{resource_id}' not found")
        else:
            builder.message(f"{resource_type.title()} not found")

        return builder

    @staticmethod
    def unauthorized(message: str = "Unauthorized access") -> "ErrorResponseBuilder":
        """
        Factory method for 401 Unauthorized errors.

        Args:
            message: Error message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("UNAUTHORIZED")
        builder.message(message)
        return builder

    @staticmethod
    def forbidden(message: str = "Forbidden") -> "ErrorResponseBuilder":
        """
        Factory method for 403 Forbidden errors.

        Args:
            message: Error message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("FORBIDDEN")
        builder.message(message)
        return builder

    @staticmethod
    def conflict(resource_type: str, message: Optional[str] = None) -> "ErrorResponseBuilder":
        """
        Factory method for 409 Conflict errors.

        Args:
            resource_type: Type of resource
            message: Custom message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("CONFLICT")

        if message:
            builder.message(message)
        else:
            builder.message(f"{resource_type.title()} already exists")

        return builder

    @staticmethod
    def server_error(message: str = "Internal server error") -> "ErrorResponseBuilder":
        """
        Factory method for 500 Internal Server Error.

        Args:
            message: Error message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("INTERNAL_ERROR")
        builder.message(message)
        return builder

    @staticmethod
    def unprocessable(
        message: str = "Unprocessable entity", details: Optional[List[tuple]] = None
    ) -> "ErrorResponseBuilder":
        """
        Factory method for 422 Unprocessable Entity errors.

        Args:
            message: Error message
            details: List of (field, error_message) tuples

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("UNPROCESSABLE_ENTITY")
        builder.message(message)

        if details:
            for field, error_msg in details:
                builder.with_field_error(field, error_msg, "VALIDATION_ERROR")

        return builder

    @staticmethod
    def rate_limited(message: str = "Rate limit exceeded") -> "ErrorResponseBuilder":
        """
        Factory method for 429 Too Many Requests errors.

        Args:
            message: Error message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("RATE_LIMIT_EXCEEDED")
        builder.message(message)
        return builder

    @staticmethod
    def service_unavailable(message: str = "Service unavailable") -> "ErrorResponseBuilder":
        """
        Factory method for 503 Service Unavailable errors.

        Args:
            message: Error message

        Returns:
            ErrorResponseBuilder instance
        """
        builder = ErrorResponseBuilder()
        builder.error_code("SERVICE_UNAVAILABLE")
        builder.message(message)
        return builder


# ============================================================================
# SUMMARY OF USAGE
# ============================================================================

"""
BEFORE (scattered error responses):
====================================

In content_routes.py:
    @app.post("/content")
    async def create_content(request: ContentRequest):
        if not request.title:
            return JSONResponse(
                status_code=400,
                content={
                    "error_code": "VALIDATION_ERROR",
                    "message": "Content title is required"
                }
            )

In task_routes.py:
    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        task = await db.fetch_task(task_id)
        if not task:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "Task not found",
                    "task_id": task_id
                }
            )

PROBLEMS:
  ❌ Inconsistent response format
  ❌ Different error codes
  ❌ Different field names
  ❌ Duplicate code
  ❌ No request ID tracking
  ❌ Hard to maintain


AFTER (standardized error responses):
======================================

In content_routes.py:
    from utils.error_responses import ErrorResponseBuilder
    
    @app.post("/content")
    async def create_content(request: ContentRequest, request_id: str):
        if not request.title:
            response = (ErrorResponseBuilder.validation_error(
                details=[("title", "Field required")]
            ).request_id(request_id)
            .path(request.url.path)
            .timestamp()
            .build_dict())
            
            return JSONResponse(status_code=400, content=response)

In task_routes.py:
    from utils.error_responses import ErrorResponseBuilder
    
    @app.get("/tasks/{task_id}")
    async def get_task(task_id: str, request_id: str):
        task = await db.fetch_task(task_id)
        if not task:
            response = (ErrorResponseBuilder.not_found("task", task_id)
                .request_id(request_id)
                .path(request.url.path)
                .timestamp()
                .build_dict())
            
            return JSONResponse(status_code=404, content=response)

BENEFITS:
  ✅ Consistent response format
  ✅ Standardized error codes
  ✅ Request ID tracking
  ✅ Path included for debugging
  ✅ Timestamps for monitoring
  ✅ Fluent, readable API
  ✅ Factory methods for common cases
  ✅ Easy to maintain
  ✅ Type-safe with Pydantic
"""
