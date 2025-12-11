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
    # Support both JWT_SECRET_KEY and JWT_SECRET for flexibility
    # SECURITY: Do NOT use hardcoded default - require environment variable
    _secret = os.getenv("JWT_SECRET_KEY") or os.getenv("JWT_SECRET")
    
    if not _secret:
        # In production, this is a critical error
        import sys
        if os.getenv("ENVIRONMENT", "development") == "production":
            print("[ERROR] JWT_SECRET_KEY or JWT_SECRET environment variable is required", file=sys.stderr)
            sys.exit(1)  # Exit if JWT secret is missing in production
        else:
            # Development fallback only - MUST MATCH frontend mockTokenGenerator.js
            _secret = "dev-jwt-secret-change-in-production-to-random-64-chars"
            print("[WARNING] Using development JWT secret - SET JWT_SECRET in .env for production", flush=True)
    
    SECRET_KEY = _secret
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    
    # Debug: log what secret was loaded (first 20 chars only for security)
    secret_preview = SECRET_KEY[:20] + "..." if len(SECRET_KEY) > 20 else "***"
    print(f"[token_validator import] JWT secret loaded: {secret_preview}", flush=True)


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
        import sys
        try:
            # Validate token format (should have 3 parts separated by dots)
            parts = token.split('.')
            if len(parts) != 3:
                raise jwt.InvalidTokenError(f"Invalid token format: expected 3 parts, got {len(parts)}")
            
            # Debug logging
            print(f"\n[verify_token] Verifying token...", file=sys.stderr)
            print(f"[verify_token] Using secret: {AuthConfig.SECRET_KEY[:30]}...", file=sys.stderr)
            print(f"[verify_token] Token: {token[:50]}...", file=sys.stderr)
            
            # Verify and decode token
            payload = jwt.decode(
                token,
                AuthConfig.SECRET_KEY,
                algorithms=[AuthConfig.ALGORITHM]
            )
            
            print(f"[verify_token] Token decoded successfully", file=sys.stderr)
            print(f"[verify_token] Payload type field: {payload.get('type')}", file=sys.stderr)
            print(f"[verify_token] Expected type: {token_type.value}", file=sys.stderr)
            
            # Verify token type
            if payload.get("type") != token_type.value:
                raise jwt.InvalidTokenError(f"Invalid token type: expected {token_type.value}")
            
            return payload
        except jwt.ExpiredSignatureError:
            raise jwt.ExpiredSignatureError("Token has expired")
        except jwt.InvalidTokenError as e:
            print(f"[verify_token] Invalid token error: {str(e)}", file=sys.stderr)
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
