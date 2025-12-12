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
from datetime import datetime, timezone, timedelta
import logging
import os
import httpx
import jwt

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from pydantic import BaseModel

from services.token_validator import JWTTokenValidator, AuthConfig
from schemas.auth_schemas import (
    UserProfile,
    LogoutResponse,
    GitHubCallbackRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# GitHub Configuration
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")


# ============================================================================
# Helper Functions
# ============================================================================

async def exchange_code_for_token(code: str) -> str:
    """Exchange GitHub authorization code for access token."""
    # Handle mock auth codes for development
    if code.startswith("mock_auth_code_"):
        logger.info("Mock auth code detected, returning mock token")
        return "mock_github_token_dev"
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.error(f"GitHub token exchange failed: {response.status_code}")
            raise HTTPException(status_code=401, detail="GitHub authentication failed")

        data = response.json()

        if "error" in data:
            logger.error(f"GitHub error: {data.get('error_description', 'Unknown error')}")
            raise HTTPException(status_code=401, detail=data.get("error_description", "GitHub authentication failed"))

        return data.get("access_token", "")


async def get_github_user(access_token: str) -> Dict[str, Any]:
    """Fetch GitHub user information using access token."""
    # Handle mock auth tokens for development
    if access_token == "mock_github_token_dev":
        logger.info("Mock token detected, returning mock user data")
        return {
            "id": 999999,
            "login": "dev-user",
            "email": "dev@example.com",
            "name": "Development User",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
            "bio": "Development user for testing"
        }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}"},
            timeout=10.0,
        )

        if response.status_code != 200:
            logger.error(f"GitHub API error: {response.status_code}")
            raise HTTPException(status_code=401, detail="Failed to fetch GitHub user")

        return response.json()


def create_jwt_token(user_data: Dict[str, Any]) -> str:
    """Create JWT token for authenticated user."""
    expiry = datetime.now(timezone.utc) + timedelta(minutes=AuthConfig.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": user_data.get("login", ""),
        "user_id": str(user_data.get("id", "")),
        "email": user_data.get("email", ""),
        "username": user_data.get("login", ""),
        "avatar_url": user_data.get("avatar_url", ""),
        "name": user_data.get("name", ""),
        "auth_provider": "github",
        "type": "access",
        "exp": expiry,
        "iat": datetime.now(timezone.utc),
    }

    token = jwt.encode(payload, AuthConfig.SECRET_KEY, algorithm=AuthConfig.ALGORITHM)
    return token


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

@router.post("/github/callback")
async def github_callback(request_data: GitHubCallbackRequest) -> Dict[str, Any]:
    """
    Handle GitHub OAuth callback.

    Receives authorization code from frontend, exchanges it for GitHub access token,
    fetches user information, and returns JWT token.
    """
    code = request_data.code
    state = request_data.state

    if not code:
        logger.warning("GitHub callback missing code parameter")
        raise HTTPException(status_code=400, detail="Missing authorization code")

    if not state:
        logger.warning("GitHub callback missing state parameter (CSRF check)")
        raise HTTPException(status_code=400, detail="Missing state parameter")

    try:
        # Exchange code for GitHub access token
        github_token = await exchange_code_for_token(code)

        # Fetch user information
        github_user = await get_github_user(github_token)

        # Create JWT token for user
        jwt_token = create_jwt_token(github_user)

        # Return token and user info
        user_info = {
            "username": github_user.get("login", ""),
            "email": github_user.get("email", ""),
            "avatar_url": github_user.get("avatar_url", ""),
            "name": github_user.get("name", ""),
            "user_id": str(github_user.get("id", "")),
            "auth_provider": "github"
        }

        logger.info(f"GitHub authentication successful for user: {user_info['username']}")

        return {
            "token": jwt_token,
            "user": user_info,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub callback error: {str(e)}")
        raise HTTPException(status_code=500, detail="Authentication error")


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
