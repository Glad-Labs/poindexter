"""
Input Validation Middleware

Automatically validates and sanitizes all incoming requests.
Provides request size limits, content-type validation, and payload inspection.
"""

import json
import logging
from typing import Callable
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class InputValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware for validating all incoming requests.
    
    Checks:
    - Request body size limits
    - Content-Type validation
    - JSON payload structure
    - Request header validation
    """
    
    # Maximum request body size (10MB)
    MAX_BODY_SIZE = 10 * 1024 * 1024
    
    # Allowed content types for non-GET requests
    ALLOWED_CONTENT_TYPES = {
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    }
    
    # Routes that skip validation (health checks, etc.)
    SKIP_VALIDATION_PATHS = {
        "/api/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Process incoming request with validation"""
        
        try:
            # Skip validation for certain paths
            if request.url.path in self.SKIP_VALIDATION_PATHS:
                return await call_next(request)
            
            # Validate request headers
            self._validate_headers(request)
            
            # Validate request body for non-GET/HEAD requests
            if request.method not in ["GET", "HEAD"]:
                await self._validate_body(request)
            
            # Validate URL path and query parameters
            self._validate_url(request)
            
            # Process request
            response = await call_next(request)
            
            return response
            
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except ValueError as e:
            # Return 400 for validation errors
            logger.warning(f"Request validation error: {str(e)}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(e)},
            )
        except Exception as e:
            # Log unexpected errors
            logger.error(f"Request validation middleware error: {str(e)}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid request"},
            )
    
    def _validate_headers(self, request: Request) -> None:
        """Validate request headers"""
        
        # Check for required headers in secure endpoints
        if request.url.path.startswith("/api/") and request.method not in ["GET", "HEAD"]:
            # Validate Content-Type
            content_type = request.headers.get("content-type", "")
            
            # Skip validation for multipart (handled by FastAPI)
            if not content_type.startswith("multipart/form-data"):
                mime_type = content_type.split(";")[0].strip()
                
                if mime_type and mime_type not in self.ALLOWED_CONTENT_TYPES:
                    raise ValueError(
                        f"Invalid Content-Type: {mime_type}. "
                        f"Allowed: {', '.join(self.ALLOWED_CONTENT_TYPES)}"
                    )
        
        # Check for suspicious headers
        suspicious_headers = [
            "x-forwarded-for",
            "x-original-url",
            "x-rewrite-url",
        ]
        
        for header in suspicious_headers:
            value = request.headers.get(header, "")
            if value and len(value) > 1000:
                raise ValueError(f"Header {header} value is too long")
    
    async def _validate_body(self, request: Request) -> None:
        """Validate request body"""
        
        # Check Content-Length header
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.MAX_BODY_SIZE:
                    raise ValueError(
                        f"Request body exceeds maximum size of {self.MAX_BODY_SIZE} bytes"
                    )
            except ValueError as e:
                if "exceeds maximum" in str(e):
                    raise
                raise ValueError("Invalid Content-Length header")
        
        # For JSON endpoints, validate JSON structure
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.body()
                
                if len(body) > self.MAX_BODY_SIZE:
                    raise ValueError(
                        f"Request body exceeds maximum size of {self.MAX_BODY_SIZE} bytes"
                    )
                
                # Validate JSON is parseable
                if body:
                    json.loads(body)
                    
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {str(e)}")
            except Exception as e:
                if "exceeds maximum" in str(e):
                    raise
                raise ValueError(f"Error reading request body: {str(e)}")
    
    def _validate_url(self, request: Request) -> None:
        """Validate URL path and query parameters"""
        
        # Check path length
        if len(request.url.path) > 2048:
            raise ValueError("Request path is too long")
        
        # Check for null bytes in path
        if "\x00" in request.url.path:
            raise ValueError("Invalid character in request path")
        
        # Check query string length
        if request.url.query:
            if len(request.url.query) > 4096:
                raise ValueError("Query string is too long")
            
            # Check for null bytes in query
            if "\x00" in request.url.query:
                raise ValueError("Invalid character in query string")
        
        # Check for suspicious path patterns
        suspicious_patterns = [
            "../",  # Path traversal
            "..\\",  # Windows path traversal
            "%00",  # Null byte encoding
            "\\x00",  # Hex null byte
        ]
        
        full_path = str(request.url)
        for pattern in suspicious_patterns:
            if pattern in full_path:
                raise ValueError(f"Suspicious pattern detected in request: {pattern}")


class PayloadInspectionMiddleware(BaseHTTPMiddleware):
    """
    Middleware for inspecting and logging request payloads for security purposes.
    Can detect suspicious patterns and log them for analysis.
    """
    
    # Log payloads for these paths for analysis
    MONITORED_PATHS = {
        "/api/tasks",
        "/api/webhooks",
        "/api/content",
    }
    
    async def dispatch(self, request: Request, call_next: Callable) -> Callable:
        """Inspect request payloads"""
        
        try:
            # Only inspect monitored paths
            if request.url.path not in self.MONITORED_PATHS:
                return await call_next(request)
            
            # Only inspect POST/PUT/PATCH requests
            if request.method not in ["POST", "PUT", "PATCH"]:
                return await call_next(request)
            
            # Read body for inspection
            body = await request.body()
            
            if body and "application/json" in request.headers.get("content-type", ""):
                try:
                    payload = json.loads(body)
                    
                    # Check for suspicious patterns in payload
                    self._check_payload(payload, request.url.path)
                    
                except json.JSONDecodeError:
                    # Invalid JSON already caught by InputValidationMiddleware
                    pass
            
            # Continue processing
            response = await call_next(request)
            return response
            
        except Exception as e:
            logger.error(f"Payload inspection error: {str(e)}", exc_info=True)
            # Don't block request on inspection errors
            return await call_next(request)
    
    def _check_payload(self, payload: dict, path: str) -> None:
        """Check payload for suspicious patterns"""
        
        # Convert to string for pattern matching
        payload_str = json.dumps(payload)
        
        # Check for SQL injection patterns
        if any(pattern in payload_str.upper() for pattern in [
            "UNION", "SELECT", "INSERT", "DROP", "DELETE", "--"
        ]):
            logger.warning(f"Suspicious SQL pattern detected in {path}")
        
        # Check for XSS patterns
        if any(pattern in payload_str for pattern in [
            "<script", "javascript:", "onerror=", "onclick="
        ]):
            logger.warning(f"Suspicious XSS pattern detected in {path}")
