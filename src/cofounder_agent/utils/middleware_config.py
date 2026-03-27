"""
Middleware Configuration - Centralized middleware setup for FastAPI application

Configures:
- CORS (Cross-Origin Resource Sharing)
- Input validation and payload inspection
- Rate limiting (slowapi)
- Security headers

All middleware can be optionally enabled/disabled and configured via environment variables.
"""

import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from services.logger_config import get_logger

logger = get_logger(__name__)


class MiddlewareConfig:
    """Manages middleware configuration and registration"""

    def __init__(self):
        self.limiter = None
        self.profiling_middleware = None

    def register_all_middleware(self, app: FastAPI) -> None:
        """
        Register all middleware with the FastAPI application.

        Middleware is registered in reverse order of actual execution
        (middleware added last is executed first).

        Order of execution (first to last):
        1. Profiling middleware (tracks request latency)
        2. CORS middleware (handles cross-origin requests)
        3. Token validation (validates JWT tokens on protected endpoints)
        4. Rate limiting (protects against abuse)
        5. Input validation (sanitizes requests)
        6. Payload inspection (logs payloads for debugging)

        Args:
            app: FastAPI application instance

        Example:
            from utils.middleware_config import MiddlewareConfig

            app = FastAPI()
            middleware_config = MiddlewareConfig()
            middleware_config.register_all_middleware(app)
        """
        # Register in reverse order (last added = first executed)
        # Profiling should execute FIRST, so it's added LAST
        self._setup_cache_control(app)
        self._setup_input_validation(app)
        self._setup_rate_limiting(app)
        self._setup_token_validation(app)
        self._setup_security_headers(app)
        self._setup_cors(app)
        # Request ID must execute before profiling so the ID is available when
        # the profiling middleware logs request completion.
        self._setup_request_id(app)
        self._setup_profiling(app)

        logger.info("✅ All middleware registered successfully")

    def _setup_request_id(self, app: FastAPI) -> None:
        """
        Setup request ID middleware and logging filter.

        - Generates / propagates X-Request-ID for every request.
        - Injects request_id into every log record via a stdlib logging.Filter.
        """
        import logging

        from middleware.request_id import RequestIDFilter, RequestIDMiddleware

        # Apply the filter to the root logger so ALL loggers inherit it.
        # Using addFilter on root ensures it propagates to every child logger.
        logging.getLogger().addFilter(RequestIDFilter())

        app.add_middleware(RequestIDMiddleware)
        logger.info("Request ID middleware initialized")

    def _setup_profiling(self, app: FastAPI) -> None:
        """
        Setup performance profiling middleware.

        Tracks request latency and identifies slow endpoints.
        Data is accessible via /api/profiling endpoints.
        """
        try:
            from middleware.profiling_middleware import ProfilingMiddleware

            self.profiling_middleware = ProfilingMiddleware(app)
            app.add_middleware(ProfilingMiddleware)

            # Store middleware reference in app state for route access
            app.state.profiling_middleware = self.profiling_middleware

            logger.info("✅ Profiling middleware initialized")
        except ImportError as e:
            logger.warning(f"⚠️  Profiling middleware not available: {e}", exc_info=True)

    def _setup_cache_control(self, app: FastAPI) -> None:
        """
        Setup HTTP Cache-Control middleware.

        Sets Cache-Control headers on all responses based on route category:
        - Mutations (POST/PUT/PATCH/DELETE): no-store
        - Auth / WebSocket routes: no-store
        - Private data (tasks, workflows, user): private, max-age=60
        - Public content (posts, cms, analytics): public, max-age=300
        """
        from middleware.cache_control import CacheControlMiddleware

        app.add_middleware(CacheControlMiddleware)
        logger.info("✅ Cache-Control middleware initialized")

    def _setup_input_validation(self, app: FastAPI) -> None:
        """
        Setup input validation and payload inspection middleware.

        Validates and sanitizes all incoming requests to prevent:
        - SQL injection
        - XSS attacks
        - Oversized payloads
        - Invalid data types
        """
        try:
            from middleware.input_validation import (
                InputValidationMiddleware,
                PayloadInspectionMiddleware,
            )

            # Add in reverse order (PayloadInspection will execute first)
            app.add_middleware(PayloadInspectionMiddleware)
            app.add_middleware(InputValidationMiddleware)

            logger.info("✅ Input validation middleware initialized")
        except ImportError as e:
            logger.warning(f"⚠️  Input validation middleware not available: {e}", exc_info=True)

    def _setup_cors(self, app: FastAPI) -> None:
        """
        Setup CORS (Cross-Origin Resource Sharing) middleware.

        Configuration is environment-based for security.

        Default development origins (localhost, 127.0.0.1 on ports 3000-3004):
        - http://localhost:3000 (Next.js public site)
        - http://localhost:3001 (React oversight hub)
        - http://127.0.0.1:3000-3004 (alternative localhost addresses)

        For production, set ALLOWED_ORIGINS env var to comma-separated list:
        ALLOWED_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

        ⚠️  WARNING: Never use allow_origins=["*"] in production!
        """
        # Get allowed origins from environment, with safe defaults
        # Includes ports 3000, 3001, 3002, 3003, 3004 for development (in case of port conflicts)
        allowed_origins = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,http://localhost:3004,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,http://127.0.0.1:3003,http://127.0.0.1:3004",  # Development defaults
        ).split(",")

        # Strip whitespace and trailing slashes from origins
        allowed_origins = [origin.strip().rstrip("/") for origin in allowed_origins]

        # Log warning if allowing many origins (likely development mode)
        if len(allowed_origins) > 2:
            logger.info(f"  ⚠️  Development mode: Allowing {len(allowed_origins)} origins")

        logger.info(f"  CORS Origins: {allowed_origins}")

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            # SECURITY: Restricted from ["*"] to specific methods
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            # SECURITY: Explicit header list required — allow_headers=["*"] with
            # allow_credentials=True violates the CORS spec (#220)
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Accept",
                "Origin",
                "X-Requested-With",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )

        logger.info("✅ CORS middleware initialized")

    def _setup_security_headers(self, app: FastAPI) -> None:
        """
        Setup HTTP security response headers.

        Adds defense-in-depth headers to all API responses:
        - X-Content-Type-Options: nosniff — prevents MIME-sniffing
        - X-Frame-Options: DENY — prevents clickjacking
        - Referrer-Policy: strict-origin-when-cross-origin
        - X-XSS-Protection: 0 — disable legacy XSS auditors (CSP is preferred)
        """

        class SecurityHeadersMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                response: Response = await call_next(request)
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                # Disable legacy XSS auditor; CSP is the modern protection
                response.headers["X-XSS-Protection"] = "0"
                # HSTS: enforce HTTPS for 1 year, include subdomains
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
                return response

        app.add_middleware(SecurityHeadersMiddleware)
        logger.info("✅ Security headers middleware initialized")

    def _setup_rate_limiting(self, app: FastAPI) -> None:
        """
        Setup rate limiting middleware to protect against:
        - DDoS attacks
        - API abuse
        - Brute force attacks

        Uses slowapi library for efficient rate limiting.
        """
        try:
            from slowapi.errors import RateLimitExceeded

            # Use the shared singleton so route @limiter.limit() decorators
            # reference the same instance that is registered with app.state.
            from utils.rate_limiter import limiter as _limiter

            self.limiter = _limiter

            # Store limiter in app state for use in route decorators
            app.state.limiter = self.limiter

            # Register rate limit exceeded handler
            @app.exception_handler(RateLimitExceeded)
            async def rate_limit_handler(request, exc):
                """Handle rate limit exceeded errors"""
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Too many requests."},
                )

            logger.info("✅ Rate limiting middleware initialized (slowapi)")

        except ImportError:
            logger.warning(
                "⚠️  slowapi not installed - rate limiting disabled. "
                "Install with: pip install slowapi",
                exc_info=True,
            )
            self.limiter = None

    def _setup_token_validation(self, app: FastAPI) -> None:
        """
        Setup token validation middleware.

        Validates JWT tokens on protected endpoints:
        - Checks Authorization header format
        - Validates token presence on protected routes
        - Full token expiration/signature validation happens at dependency level

        Phase 1 OAuth Security: Ensures tokens are present and formatted correctly
        before reaching route handlers.
        """
        try:
            from middleware.token_validation import TokenValidationMiddleware

            app.add_middleware(TokenValidationMiddleware)
            logger.info("✅ Token validation middleware initialized")
        except ImportError as e:
            logger.warning(f"⚠️  Token validation middleware not available: {e}", exc_info=True)

    def get_limiter(self):
        """
        Get the rate limiter instance for use in route decorators.

        Returns:
            Limiter instance or None if not available

        Example:
            from utils.middleware_config import middleware_config

            @app.get("/expensive-endpoint")
            @middleware_config.get_limiter().limit("5/minute")
            async def expensive_endpoint(request: Request):
                return {"status": "ok"}
        """
        return self.limiter


def create_middleware_config() -> MiddlewareConfig:
    """
    Factory function to create and return a MiddlewareConfig instance.

    Returns:
        MiddlewareConfig: Configured middleware manager

    Example:
        from utils.middleware_config import create_middleware_config

        middleware = create_middleware_config()
        middleware.register_all_middleware(app)
    """
    return MiddlewareConfig()


# Singleton instance for convenient access
middleware_config = create_middleware_config()
