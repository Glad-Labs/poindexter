"""
Token Validation Middleware

Validates JWT token expiration on authenticated requests.
Provides optional OAuth token status checking for Phase 1 OAuth security.

Uses:
- JWTTokenValidator for JWT token validation
- TokenManager for OAuth token status checking (optional)
"""

from collections.abc import Callable

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from services.logger_config import get_logger
from services.site_config import site_config

logger = get_logger(__name__)


class TokenValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware for validating JWT tokens on protected endpoints.

    Features:
    - Validates JWT token expiration and signature
    - Checks OAuth token status (optional, non-blocking)
    - Allows development mode token bypass via DISABLE_AUTH_FOR_DEV
    - Skips validation for public and WebSocket endpoints

    Protected Endpoints (require valid token):
    - /api/* (except public routes)

    Public Endpoints (no token required):
    - GET /api/public/*
    - /api/auth/* (login/logout)
    - /health
    """

    # Routes that require authentication
    PROTECTED_PATHS = {
        "/api/tasks",
        "/api/workflows",
        "/api/custom-workflows",
        "/api/agents",
        "/api/capability-tasks",
        "/api/bulk-tasks",
        "/api/commands",
        "/api/services",
        "/api/cms",
    }

    # Routes that don't require authentication
    PUBLIC_PATHS = {
        "/api/public",
        "/api/auth",
        "/api/cms/status",
        "/api/track",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/ws",  # WebSocket
    }

    # Paths hit by scrapers/health checks — we don't want one INFO log per
    # poll, so the dev-bypass message is demoted to DEBUG for these.
    _NOISY_PATH_PREFIXES = ("/api/prometheus", "/api/health", "/api/metrics")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process incoming request with token validation"""

        try:
            # Development mode: Allow bypassing authentication for testing.
            # Guard: DISABLE_AUTH_FOR_DEV only honoured when DEVELOPMENT_MODE=true,
            # ensuring it never works on staging or production (#1219).
            if (
                site_config.get("disable_auth_for_dev", "false").lower() == "true"
                and site_config.get("development_mode", "false").lower() == "true"
            ):
                path = request.url.path
                if path.startswith(self._NOISY_PATH_PREFIXES):
                    logger.debug(
                        "[TokenValidation] DISABLE_AUTH_FOR_DEV=true, bypassing auth for %s", path
                    )
                else:
                    logger.info(
                        "[TokenValidation] DISABLE_AUTH_FOR_DEV=true, bypassing auth for %s", path
                    )
                return await call_next(request)

            # Skip validation for WebSocket connections
            if request.headers.get("upgrade", "").lower() == "websocket":
                return await call_next(request)

            # Skip validation for public routes
            if any(request.url.path.startswith(path) for path in self.PUBLIC_PATHS):
                return await call_next(request)

            # Skip validation for protected paths not matched (let them through)
            # Validation happens at dependency level via get_current_user()
            is_protected = any(request.url.path.startswith(path) for path in self.PROTECTED_PATHS)

            if not is_protected:
                # Not a protected endpoint, proceed
                return await call_next(request)

            # For protected endpoints, validate token is present
            auth_header = request.headers.get("Authorization", "")

            if not auth_header:
                logger.warning(
                    f"[TokenValidation] Missing auth header for protected path: {request.url.path}"
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Missing authorization header"},
                )

            # Token format validation
            if not auth_header.startswith("Bearer "):
                logger.warning(
                    f"[TokenValidation] Invalid auth header format for {request.url.path}"
                )
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Invalid authorization header format. Use: Bearer <token>"},
                )

            # Extract token
            token = auth_header[7:]  # Remove "Bearer " prefix

            # Validate token using JWTTokenValidator (at dependency level in get_current_user)
            # This middleware just ensures the token is present and formatted correctly
            # Full validation (expiration, signature) happens in get_current_user()

            # Continue to next middleware/endpoint
            response = await call_next(request)
            return response

        except Exception as e:
            logger.error(
                f"[TokenValidation] Error validating token: {type(e).__name__}: {str(e)}",
                exc_info=True,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Token validation error"},
            )
