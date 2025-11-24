"""
Authentication API Routes for Glad Labs AI Co-Founder

OAUTH-ONLY ARCHITECTURE:
- Frontend handles all OAuth login (GitHub, Google, Facebook)
- API ONLY validates JWT tokens - no user creation or password management
- All login/register logic delegated to frontend + OAuth providers
- Database interactions via token validation only

DEPRECATED ENDPOINTS (Removed):
- /login, /register - OAuth replaces these
- /refresh - OAuth providers handle token refresh  
- /change-password - OAuth providers handle this
- 2FA endpoints - Not needed for OAuth

USE auth_unified.py for:
- /logout - Logout for all auth types
- /me - Get current user profile
"""

from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from services.token_validator import validate_access_token


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Dependency: Get Current User
# ============================================================================

async def get_current_user(request: Request) -> dict:
    """
    Async dependency to get current authenticated user from request.
    
    Validates JWT token from Authorization header.
    In development mode, allows requests without auth for easier testing.
    Returns user dict from database.
    """
    import os
    
    # Development mode: allow access without auth
    if os.getenv("ENVIRONMENT", "development").lower() == "development":
        # Check if token is provided
        auth_header = request.headers.get("Authorization", "")
        
        # If no token, return mock dev user for development
        if not auth_header.startswith("Bearer "):
            return {
                "id": "dev-user-123",
                "email": "dev@localhost",
                "username": "dev-user",
                "is_active": True,
                "created_at": "2025-01-01T00:00:00Z",
            }
        
        # If token provided, validate it
        token = auth_header[7:]  # Remove "Bearer " prefix
        is_valid, claims = validate_access_token(token)
        if is_valid and claims:
            user_id = claims.get("user_id", "dev-user-123")
            return {
                "id": user_id,
                "email": claims.get("email", "dev@example.com"),
                "username": claims.get("username", "dev-user"),
                "is_active": True,
                "created_at": claims.get("created_at", "2025-01-01T00:00:00Z"),
            }
        # Invalid token in dev mode still returns dev user
        return {
            "id": "dev-user-123",
            "email": "dev@localhost",
            "username": "dev-user",
            "is_active": True,
            "created_at": "2025-01-01T00:00:00Z",
        }
    
    # Production mode: require valid auth
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Validate token
    is_valid, claims = validate_access_token(token)
    if not is_valid or not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # For production: return authenticated user
    user_id = claims.get("user_id", "unknown")
    return {
        "id": user_id,
        "email": claims.get("email", "user@example.com"),
        "username": claims.get("username", "user"),
        "is_active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

# ============================================================================
# ⚠️ NOTE: All authentication endpoints removed (OAuth-only architecture)
# ============================================================================
# 
# Traditional endpoints removed:
# - POST /login - Use OAuth frontend instead
# - POST /register - Use OAuth frontend instead
# - POST /refresh - OAuth providers handle token refresh
# - POST /change-password - Use OAuth provider's account management
# - POST /setup-2fa, /verify-2fa, /disable-2fa - Not needed for OAuth
#
# Use auth_unified.py for:
# - GET /api/auth/me - Get current user profile
# - POST /api/auth/logout - Logout for all auth types
#
# All auth logic happens in the frontend with OAuth providers.
# API only validates JWT tokens from OAuth providers.
# ============================================================================


@router.get("/backup-codes")
async def get_backup_codes(current_user: dict = Depends(get_current_user)) -> dict:
    """Get backup codes (STUB)"""
    return {"success": True, "codes": []}


@router.post("/regenerate-backup-codes")
async def regenerate_backup_codes(current_user: dict = Depends(get_current_user)) -> dict:
    """Regenerate backup codes (STUB)"""
    return {"success": True, "message": "Backup codes regenerated"}
