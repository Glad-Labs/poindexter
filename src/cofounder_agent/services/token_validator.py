"""
Token Validator Service for OAuth-Only API

Minimal service for validating JWT tokens from frontend OAuth.
API ONLY validates tokens, does not create or manage them.

This is a standalone validator that:
- Validates JWT tokens from Authorization header
- Returns user claims if valid
- No database dependencies (stateless validation)
- No user creation or session management
"""

import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import jwt

from services.logger_config import get_logger

_logger = get_logger(__name__)


class TokenType(str, Enum):
    """Token types"""

    ACCESS = "access"
    REFRESH = "refresh"


class AuthConfig:
    """Auth configuration - minimal version for validation only"""

    # Support both JWT_SECRET_KEY and JWT_SECRET for flexibility
    # SECURITY: Do NOT use hardcoded default - require environment variable
    _from_env = os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET")

    if not _from_env:
        # In production, this is a critical error
        import sys

        if os.getenv("ENVIRONMENT", "development") == "production":
            _logger.critical(
                "[AuthConfig] JWT_SECRET_KEY or JWT_SECRET environment variable is required"
            )
            sys.exit(1)  # Exit if JWT secret is missing in production
        else:
            # Development fallback - MUST MATCH .env.local JWT_SECRET value
            _from_env = "development-secret-key-change-in-production"
            _logger.warning(
                "[AuthConfig] Using development JWT secret — set JWT_SECRET in .env for production"
            )
            _secret_source = "FALLBACK (hardcoded development)"
    else:
        _secret_source = "JWT_SECRET_KEY" if os.getenv("JWT_SECRET_KEY") else "JWT_SECRET"
        _logger.info("[AuthConfig] JWT secret loaded from %s", _secret_source)

    SECRET_KEY = _from_env
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))


class JWTTokenValidator:
    """Validates JWT tokens without database access"""

    @staticmethod
    def verify_token(
        token: str, token_type: TokenType = TokenType.ACCESS
    ) -> Optional[Dict[str, Any]]:
        """
        Verify a JWT token and return claims.

        Args:
            token: JWT token string
            token_type: Expected token type

        Returns:
            Dict of claims if valid, None if invalid

        Raises:
            jwt.ExpiredSignatureError: Token has expired
            jwt.InvalidTokenError: Token is invalid
        """

        # Development: Allow disabling auth for testing — ONLY in non-production environments
        if (
            os.getenv("DISABLE_AUTH_FOR_DEV") == "true"
            and os.getenv("ENVIRONMENT", "development") != "production"
        ):
            return {
                "sub": "dev-user",
                "user_id": "dev-user-id",
                "email": "dev@example.com",
                "username": "dev-user",
                "type": token_type.value,
                "iat": datetime.now().timestamp(),
                "exp": (datetime.now().timestamp() + 3600),
            }

        try:
            # Validate token format (should have 3 parts separated by dots)
            parts = token.split(".")
            if len(parts) != 3:
                raise jwt.InvalidTokenError(
                    f"Invalid token format: expected 3 parts, got {len(parts)}"
                )

            # Verify and decode token
            payload = jwt.decode(token, AuthConfig.SECRET_KEY, algorithms=[AuthConfig.ALGORITHM])

            # Verify token type
            if payload.get("type") != token_type.value:
                raise jwt.InvalidTokenError(
                    f"Invalid token type: expected {token_type.value}, got {payload.get('type')}"
                )

            return payload
        except jwt.ExpiredSignatureError:
            raise jwt.ExpiredSignatureError("Token has expired")
        except jwt.InvalidTokenError:
            raise jwt.InvalidTokenError("Invalid token")

    @staticmethod
    def validate_access_token(token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate an access token and return claims.

        Args:
            token: JWT token string

        Returns:
            Tuple of (is_valid, claims_dict)
        """
        try:
            claims = JWTTokenValidator.verify_token(token, TokenType.ACCESS)
            return (True, claims)
        except jwt.ExpiredSignatureError:
            return (False, {"error": "Token expired"})
        except jwt.InvalidTokenError as e:
            return (False, {"error": f"Invalid token: {str(e)}"})


# Convenience function for existing imports
def validate_access_token(token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Convenience wrapper for token validation"""
    return JWTTokenValidator.validate_access_token(token)
