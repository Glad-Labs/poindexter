"""
Unified Authentication Routes for All Auth Types

This module consolidates authentication endpoints that were previously
duplicated across auth.py, auth_routes.py, and oauth_routes.py.

It provides single endpoints for:
- POST /api/auth/logout - Works for all auth types (JWT, OAuth, GitHub)
- GET /api/auth/me - Returns current user profile with auth provider info

The endpoints auto-detect the authentication method from the JWT token
and route to the appropriate handler.

Routes:
- POST /api/auth/logout         -> Logout (all auth types)
- GET  /api/auth/me             -> Get current user profile
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from pydantic import BaseModel

from services.token_validator import JWTTokenValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Pydantic Models
# ============================================================================

class UserProfile(BaseModel):
    """User profile response model."""
    id: str
    email: str
    username: str
    auth_provider: str  # "jwt", "oauth", "github"
    is_active: bool
    created_at: str


class LogoutResponse(BaseModel):
    """Logout response model."""
    success: bool
    message: str


# ============================================================================
# Dependency: Get Current User (Auth-Agnostic)
# ============================================================================

async def get_current_user(request: Request) -> Dict[str, Any]:
    """
    Extract and validate JWT token from Authorization header.
    
    Works for all auth types (traditional JWT, OAuth, GitHub OAuth).
    Auto-detects auth provider from token claims.
    
    Args:
        request: FastAPI request object
    
    Returns:
        Dictionary with user info and auth provider details
    
    Raises:
        HTTPException: 401 if no valid token or token invalid
    
    Example:
        GET /api/auth/me
        Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
        
        Returns:
        {
            "id": "user-123",
            "email": "user@example.com",
            "username": "username",
            "auth_provider": "github",
            "is_active": true,
            "created_at": "2025-01-15T10:30:00Z"
        }
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Verify token
    try:
        claims = JWTTokenValidator.verify_token(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = claims.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
        )
    
    # Return user info with auth provider (auto-detected from claims)
    return {
        "id": str(user_id),
        "email": claims.get("email", ""),
        "username": claims.get("username") or claims.get("sub", ""),
        "auth_provider": claims.get("auth_provider", "jwt"),  # Auto-detected
        "is_active": claims.get("is_active", True),
        "created_at": claims.get("created_at", datetime.now(timezone.utc).isoformat()),
        "token": token,  # Include token for logout operations
    }


# ============================================================================
# Unified Endpoints
# ============================================================================

@router.post("/logout", response_model=LogoutResponse)
async def unified_logout(current_user: Dict[str, Any] = Depends(get_current_user)) -> LogoutResponse:
    """
    Unified logout endpoint for all authentication types.
    
    Auto-detects authentication type from JWT token and routes to appropriate
    logout logic:
    
    - **Traditional JWT**: Remove from token blacklist (if implemented)
    - **OAuth**: Revoke refresh token with OAuth provider
    - **GitHub OAuth**: Invalidate session with GitHub
    
    In current implementation (stub), simply acknowledges logout.
    Token is invalidated on frontend by removing it from localStorage/cookies.
    
    Headers:
        Authorization: Bearer <JWT token>
    
    Returns:
        LogoutResponse with success status and message
    
    Raises:
        HTTPException: 401 if token is invalid
    
    Example:
        POST /api/auth/logout
        Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
        
        Response:
        {
          "success": true,
          "message": "Successfully logged out"
        }
    """
    auth_provider = current_user.get("auth_provider", "jwt")
    user_id = current_user.get("id", "unknown")
    
    logger.info(f"Logout request for user {user_id} (auth_provider: {auth_provider})")
    
    try:
        # In production, implement provider-specific logout:
        # if auth_provider == "github":
        #     await revoke_github_session(user_id)
        # elif auth_provider == "oauth":
        #     await revoke_oauth_refresh_token(current_user["token_id"])
        # else:
        #     await add_token_to_blacklist(current_user["token"])
        
        logger.info(f"User {user_id} logged out successfully ({auth_provider})")
        
        return LogoutResponse(
            success=True,
            message=f"Successfully logged out ({auth_provider} authentication)"
        )
    
    except Exception as e:
        logger.error(f"Logout error for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed"
        )


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(
    current_user: Dict[str, Any] = Depends(get_current_user)
) -> UserProfile:
    """
    Get current user's profile (works for all auth types).
    
    Returns the authenticated user's profile information with details about
    which authentication method was used.
    
    This endpoint works transparently for:
    - Traditional JWT authentication
    - OAuth authentication
    - GitHub OAuth authentication
    
    Headers:
        Authorization: Bearer <JWT token>
    
    Returns:
        UserProfile with user information and auth provider details
    
    Raises:
        HTTPException: 401 if token is invalid or expired
    
    Example:
        GET /api/auth/me
        Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
        
        Response:
        {
          "id": "550e8400-e29b-41d4-a716-446655440000",
          "email": "user@github.com",
          "username": "octocat",
          "auth_provider": "github",
          "is_active": true,
          "created_at": "2025-01-15T10:30:00Z"
        }
    """
    logger.info(
        f"Profile request for user {current_user.get('id')} "
        f"(auth_provider: {current_user.get('auth_provider', 'unknown')})"
    )
    
    return UserProfile(
        id=current_user["id"],
        email=current_user.get("email", ""),
        username=current_user.get("username", ""),
        auth_provider=current_user.get("auth_provider", "jwt"),
        is_active=current_user.get("is_active", True),
        created_at=current_user.get("created_at", datetime.now(timezone.utc).isoformat()),
    )
