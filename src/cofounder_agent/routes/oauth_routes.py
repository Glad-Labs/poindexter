"""
OAuth Authentication API Routes - OAuth-Only Implementation.

This routes module handles OAuth provider authentication flows.
Currently supports GitHub, easily extensible to add Google, Facebook, etc.

Routes:
- GET  /api/auth/{provider}/login       -> Redirect to OAuth provider
- GET  /api/auth/{provider}/callback    -> Handle OAuth callback
- GET  /api/auth/me                     -> Get current user profile
- POST /api/auth/logout                 -> Logout user
- GET  /api/auth/providers              -> List available OAuth providers
"""

import os
import secrets
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from starlette.responses import RedirectResponse
import httpx

from services.oauth_manager import OAuthManager, OAuthException
from services.auth import create_access_token, verify_token
from models import User, OAuthAccount
from services.database_service import DatabaseService


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ============================================================================
# Pydantic Models
# ============================================================================

class UserProfile(BaseModel):
    """User profile response model."""
    id: str
    email: str
    username: str
    is_active: bool
    created_at: str


class OAuthCallbackRequest(BaseModel):
    """OAuth provider callback data."""
    code: str
    state: str


class TokenResponse(BaseModel):
    """OAuth token response."""
    access_token: str
    token_type: str = "bearer"
    user: UserProfile


class ProvidersResponse(BaseModel):
    """Available OAuth providers."""
    providers: list[str]


# ============================================================================
# Dependency: Get Current User
# ============================================================================

async def get_current_user(request: Request, db: DatabaseService = Depends(DatabaseService)) -> User:
    """
    Get current authenticated user from JWT token in Authorization header.
    
    Args:
        request: FastAPI request
        db: Database service
    
    Returns:
        User object
    
    Raises:
        HTTPException: 401 if no valid token
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    # Verify token (from auth_service.py)
    is_valid, claims = verify_token(token)
    if not is_valid or not claims:
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
    
    # Get user from database
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    return user


# ============================================================================
# State Management (CSRF Protection)
# ============================================================================

# In production, store in Redis or database
# For now, store in memory (single-instance only)
_oauth_states = {}


def generate_state() -> str:
    """Generate CSRF protection state."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = datetime.now(timezone.utc).timestamp()
    return state


def validate_state(state: str) -> bool:
    """Validate CSRF state (should be used within 10 minutes)."""
    if state not in _oauth_states:
        return False
    
    timestamp = _oauth_states[state]
    elapsed = datetime.now(timezone.utc).timestamp() - timestamp
    
    # State valid for 10 minutes
    if elapsed > 600:
        del _oauth_states[state]
        return False
    
    del _oauth_states[state]  # Use once
    return True


# ============================================================================
# OAuth Endpoints
# ============================================================================

@router.get("/{provider}/login")
async def oauth_login(provider: str):
    """
    Step 1: Redirect user to OAuth provider for authorization.
    
    This endpoint generates the authorization URL for the specified provider
    and redirects the user to log in with that provider.
    
    Args:
        provider: OAuth provider name ('github', 'google', 'facebook', etc.)
    
    Returns:
        Redirect to provider's login page
    
    Raises:
        HTTPException: 400 if provider not found
    
    Example:
        User clicks "Login with GitHub"
        → Browser goes to /api/auth/github/login
        → Redirects to https://github.com/login/oauth/authorize?...
    """
    try:
        # Validate provider exists
        OAuthManager.get_provider(provider)
    except OAuthException as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Generate CSRF protection state
    state = generate_state()
    
    # Get authorization URL from provider
    try:
        auth_url = OAuthManager.get_authorization_url(provider, state)
    except OAuthException as e:
        raise HTTPException(status_code=500, detail=f"OAuth provider error: {str(e)}")
    
    # Redirect user to provider
    return RedirectResponse(url=auth_url)


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    db: DatabaseService = Depends(DatabaseService),
):
    """
    Step 2 & 3: Handle OAuth provider callback.
    
    This endpoint receives the authorization code from the OAuth provider,
    exchanges it for an access token, fetches user information, and creates/updates
    the user account in our database.
    
    Args:
        provider: OAuth provider name
        code: Authorization code from provider
        state: CSRF state token (must match what we sent)
        db: Database service
    
    Returns:
        Redirect to frontend with access token in URL
    
    Example:
        GitHub redirects to /api/auth/github/callback?code=abc123&state=xyz
        → Exchange code for access token
        → Fetch user info from GitHub
        → Create/update user in database
        → Generate JWT token
        → Redirect to frontend: https://app.example.com?token=jwt_token
    """
    # Validate CSRF state
    if not validate_state(state):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state parameter (CSRF token mismatch)"
        )
    
    # Validate provider
    try:
        OAuthManager.get_provider(provider)
    except OAuthException as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    try:
        # Step 1: Exchange code for access token
        access_token = OAuthManager.exchange_code_for_token(provider, code)
        
        # Step 2: Fetch user info from OAuth provider
        oauth_user = OAuthManager.get_user_info(provider, access_token)
        
        # Step 3: Get or create user in our database
        user = await db.get_or_create_oauth_user(oauth_user)
        
        # Generate JWT token for our app
        jwt_token = create_access_token(
            user_id=str(user.id),
            username=user.username,
            email=user.email
        )
        
        # Redirect to frontend with token
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        callback_path = os.getenv("OAUTH_CALLBACK_PATH", "auth/callback")
        
        return RedirectResponse(
            url=f"{frontend_url}/{callback_path}?token={jwt_token}&provider={provider}"
        )
    
    except OAuthException as e:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth authentication failed: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Server error during OAuth processing: {str(e)}"
        )


# NOTE: GET /me endpoint moved to routes/auth_unified.py
# (unified endpoint that works for all auth types: JWT, OAuth, GitHub)
# See: routes/auth_unified.py for consolidated implementation


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout user (revoke token).
    
    In OAuth-only mode, logout simply invalidates the JWT token.
    Token revocation can be tracked in a token blacklist if needed.
    
    Returns:
        Success message
    """
    # In OAuth mode, logout is handled client-side by removing token
    # Optionally: add token to blacklist/revocation list
    return {
        "success": True,
        "message": "Logged out successfully"
    }


@router.get("/providers", response_model=ProvidersResponse)
async def list_oauth_providers():
    """
    List available OAuth providers.
    
    Returns:
        List of provider names that can be used for login
    
    Example:
        GET /api/auth/providers
        
        Response:
        {
          "providers": ["github", "google", "facebook"]
        }
    """
    return ProvidersResponse(
        providers=OAuthManager.list_providers()
    )


# ============================================================================
# Account Linking (Future: Connect multiple OAuth accounts)
# ============================================================================

@router.post("/{provider}/link")
async def link_oauth_account(
    provider: str,
    current_user: User = Depends(get_current_user)
):
    """
    Link additional OAuth provider to existing account.
    
    This allows a user to connect their GitHub and Google accounts
    to the same Glad Labs account.
    
    Future implementation.
    """
    return {
        "error": "Account linking not yet implemented",
        "status": "coming_soon"
    }


@router.delete("/{provider}/unlink")
async def unlink_oauth_account(
    provider: str,
    current_user: User = Depends(get_current_user)
):
    """
    Unlink OAuth provider from account.
    
    Remove an OAuth provider connection if user has multiple providers linked.
    
    Future implementation.
    """
    return {
        "error": "Account unlinking not yet implemented",
        "status": "coming_soon"
    }
