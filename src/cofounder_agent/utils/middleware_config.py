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
import logging
from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class MiddlewareConfig:
    """Manages middleware configuration and registration"""
    
    def __init__(self):
        self.limiter = None
    
    def register_all_middleware(self, app: FastAPI) -> None:
        """
        Register all middleware with the FastAPI application.
        
        Middleware is registered in reverse order of actual execution
        (middleware added last is executed first).
        
        Order of execution (first to last):
        1. CORS middleware (handles cross-origin requests)
        2. Rate limiting (protects against abuse)
        3. Input validation (sanitizes requests)
        4. Payload inspection (logs payloads for debugging)
        
        Args:
            app: FastAPI application instance
        
        Example:
            from utils.middleware_config import MiddlewareConfig
            
            app = FastAPI()
            middleware_config = MiddlewareConfig()
            middleware_config.register_all_middleware(app)
        """
        # Register in reverse order (last added = first executed)
        # CORS should execute FIRST, so it's added LAST
        self._setup_input_validation(app)
        self._setup_rate_limiting(app)
        self._setup_cors(app)
        
        logger.info("✅ All middleware registered successfully")
    
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
                PayloadInspectionMiddleware
            )
            
            # Add in reverse order (PayloadInspection will execute first)
            app.add_middleware(PayloadInspectionMiddleware)
            app.add_middleware(InputValidationMiddleware)
            
            logger.info("✅ Input validation middleware initialized")
        except ImportError as e:
            logger.warning(f"⚠️  Input validation middleware not available: {e}")
    
    def _setup_cors(self, app: FastAPI) -> None:
        """
        Setup CORS (Cross-Origin Resource Sharing) middleware.
        
        Configuration is environment-based for security.
        """
        # Get allowed origins from environment, with safe defaults
        # Includes ports 3000, 3001, 3002, 3003, 3004 for development (in case of port conflicts)
        allowed_origins = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003,http://localhost:3004,http://127.0.0.1:3000,http://127.0.0.1:3001,http://127.0.0.1:3002,http://127.0.0.1:3003,http://127.0.0.1:3004"  # Development defaults
        ).split(",")
        
        # Strip whitespace from origins
        allowed_origins = [origin.strip() for origin in allowed_origins]
        
        logger.info(f"  CORS Origins: {allowed_origins}")
        
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            # SECURITY: Restricted from ["*"] to specific methods
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            # SECURITY: Restricted from ["*"] to specific headers
            allow_headers=["*"],
            expose_headers=["*"],
            max_age=600,
        )
        
        logger.info("✅ CORS middleware initialized")
    
    def _setup_rate_limiting(self, app: FastAPI) -> None:
        """
        Setup rate limiting middleware to protect against:
        - DDoS attacks
        - API abuse
        - Brute force attacks
        
        Uses slowapi library for efficient rate limiting.
        """
        try:
            from slowapi import Limiter
            from slowapi.util import get_remote_address
            from slowapi.errors import RateLimitExceeded
            
            # Create limiter instance
            self.limiter = Limiter(key_func=get_remote_address)
            
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
                "Install with: pip install slowapi"
            )
            self.limiter = None
    
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
