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
from typing import Optional, Tuple, Dict, Any
from enum import Enum
import jwt


class TokenType(str, Enum):
    """Token types"""
    ACCESS = "access"
    REFRESH = "refresh"


class AuthConfig:
    """Auth configuration - minimal version for validation only"""
    SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))


class JWTTokenValidator:
    """Validates JWT tokens without database access"""

    @staticmethod
    def verify_token(token: str, token_type: TokenType = TokenType.ACCESS) -> Optional[Dict[str, Any]]:
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
        try:
            # Verify and decode token
            payload = jwt.decode(
                token,
                AuthConfig.SECRET_KEY,
                algorithms=[AuthConfig.ALGORITHM]
            )
            
            # Verify token type
            if payload.get("type") != token_type.value:
                raise jwt.InvalidTokenError(f"Invalid token type: expected {token_type.value}")
            
            return payload
        except jwt.ExpiredSignatureError:
            raise jwt.ExpiredSignatureError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise jwt.InvalidTokenError(f"Invalid token: {str(e)}")

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
