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
    """Auth configuration - minimal version for validation only.

    Reads JWT_SECRET_KEY / JWT_SECRET from os.environ.  If neither is set,
    config/__init__.py will have auto-generated one before this module loads
    (get_config() is called at top of main.py).  The development fallback
    remains for the rare case this module is imported before get_config().
    """

    _from_env = os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET")

    if not _from_env:
        # Auto-generate if missing (config/__init__.py normally does this first)
        import secrets as _secrets

        _from_env = _secrets.token_urlsafe(48)
        os.environ["JWT_SECRET_KEY"] = _from_env
        os.environ["JWT_SECRET"] = _from_env
        _logger.warning(
            "[AuthConfig] JWT secret not found — auto-generated. "
            "Set JWT_SECRET_KEY env var for stable tokens across restarts."
        )
        _secret_source = "AUTO-GENERATED"
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

        # Development: Allow disabling auth for testing — ONLY when DEVELOPMENT_MODE=true (#1219)
        if (
            os.getenv("DISABLE_AUTH_FOR_DEV", "false").lower() == "true"
            and os.getenv("DEVELOPMENT_MODE", "false").lower() == "true"
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
        except jwt.ExpiredSignatureError as exc:
            raise jwt.ExpiredSignatureError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise jwt.InvalidTokenError("Invalid token") from exc

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
