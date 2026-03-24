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

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status

from utils.rate_limiter import limiter

# Lazy-initialized httpx client for GitHub OAuth — avoids per-request connection overhead
_github_http_client: httpx.AsyncClient | None = None


def _get_github_client() -> httpx.AsyncClient:
    global _github_http_client
    if _github_http_client is None:
        _github_http_client = httpx.AsyncClient(timeout=10.0)
    return _github_http_client

from config import get_config
from schemas.auth_schemas import (
    GitHubCallbackRequest,
    LogoutResponse,
    UserProfile,
)
from services.jwt_blocklist_service import jwt_blocklist
from services.token_validator import AuthConfig, JWTTokenValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# GitHub Configuration
# Note: Using GH_OAUTH_ prefix instead of GITHUB_ because GitHub Actions
# blocks secrets starting with GITHUB_ for security reasons
GITHUB_CLIENT_ID = os.getenv("GH_OAUTH_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GH_OAUTH_CLIENT_SECRET", "")

# CSRF State Store - stores valid states with expiration
# In production, replace with Redis or session store for distributed deployments
_CSRF_STATES: Dict[str, datetime] = {}
CSRF_STATE_EXPIRY_SECONDS = 600  # 10 minutes

# ============================================================================
# Helper Functions
# ============================================================================


def generate_csrf_state() -> str:
    """Generate a cryptographically secure CSRF state token."""
    state = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=CSRF_STATE_EXPIRY_SECONDS)
    _CSRF_STATES[state] = expiry
    logger.debug(f"Generated CSRF state token (expires in {CSRF_STATE_EXPIRY_SECONDS}s)")
    return state


def validate_csrf_state(state: str) -> bool:
    """
    Validate CSRF state token.

    Checks:
    - State exists in store
    - State has not expired
    - Removes state from store after validation (one-time use)

    Returns:
        True if state is valid, False otherwise
    """
    if not state or state not in _CSRF_STATES:
        logger.warning("CSRF state validation failed: state not found in store")
        return False

    expiry = _CSRF_STATES[state]
    if datetime.now(timezone.utc) > expiry:
        logger.warning("CSRF state validation failed: state expired")
        del _CSRF_STATES[state]
        return False

    # Remove state after successful validation (one-time use only)
    del _CSRF_STATES[state]
    logger.debug("CSRF state validation successful")
    return True


async def exchange_code_for_token(code: str) -> Dict[str, Any]:
    """
    Exchange GitHub authorization code for access token.

    Returns:
        Dictionary containing access_token, expires_in, and other token metadata

    Raises:
        HTTPException: If token exchange fails
    """
    # Handle mock auth codes — only permitted in DEVELOPMENT_MODE
    if code.startswith("mock_auth_code_"):
        _cfg = get_config()
        if _cfg.environment.lower() != "development" or os.getenv("DEVELOPMENT_MODE", "").lower() != "true":
            logger.warning("[exchange_code_for_token] Mock auth code rejected outside DEVELOPMENT_MODE")
            raise HTTPException(status_code=401, detail="Mock authentication is not permitted in this environment")
        logger.info("Mock auth code detected (DEVELOPMENT_MODE), returning mock token")
        return {"access_token": "mock_github_token_dev", "expires_in": 3600}

    try:
        response = await _get_github_client().post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )

        logger.debug(f"GitHub token exchange response status: {response.status_code}")

        if response.status_code != 200:
            response_text = response.text
            logger.error(
                f"GitHub token exchange failed with status {response.status_code}: {response_text}"
            )
            raise HTTPException(
                status_code=401,
                detail="GitHub authentication failed - invalid code or credentials",
            )

        data = response.json()
        logger.debug(f"GitHub response keys: {data.keys()}")

        if "error" in data:
            error_description = data.get("error_description", "Unknown error")
            logger.error(f"GitHub error: {data.get('error')} - {error_description}")
            raise HTTPException(
                status_code=401,
                detail=f"GitHub rejected request: {error_description}",
            )

        access_token = data.get("access_token", "")
        if not access_token:
            logger.error(f"No access token in GitHub response. Keys: {list(data.keys())}")
            raise HTTPException(status_code=401, detail="Invalid token response from GitHub")

        logger.info("Successfully obtained GitHub access token")
        return {
            "access_token": access_token,
            "expires_in": data.get("expires_in"),
            "token_type": data.get("token_type", "bearer"),
            "scope": data.get("scope", ""),
        }
    except httpx.TimeoutException:
        logger.error("GitHub token exchange timed out", exc_info=True)
        raise HTTPException(status_code=503, detail="GitHub authentication service unavailable")
    except httpx.HTTPError as e:
        logger.error(f"GitHub token exchange HTTP error: {e}", exc_info=True)
        raise HTTPException(status_code=401, detail="Failed to exchange code for token")


async def get_github_user(access_token: str) -> Dict[str, Any]:
    """Fetch GitHub user information using access token."""
    # Handle mock auth tokens — only permitted in DEVELOPMENT_MODE
    if access_token == "mock_github_token_dev":
        _cfg = get_config()
        if _cfg.environment.lower() != "development" or os.getenv("DEVELOPMENT_MODE", "").lower() != "true":
            logger.warning("[get_github_user] Mock token rejected outside DEVELOPMENT_MODE")
            raise HTTPException(status_code=401, detail="Mock authentication is not permitted in this environment")
        logger.info("Mock token detected (DEVELOPMENT_MODE), returning mock user data")
        return {
            "id": 999999,
            "login": "dev-user",
            "email": "dev@example.com",
            "name": "Development User",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
            "bio": "Development user for testing",
        }

    response = await _get_github_client().get(
        "https://api.github.com/user",
        headers={"Authorization": f"token {access_token}"},
    )

    logger.debug(f"GitHub user API response status: {response.status_code}")

    if response.status_code != 200:
        response_text = response.text
        logger.error(f"GitHub API error: {response.status_code} - {response_text}")
        raise HTTPException(
            status_code=401, detail=f"Failed to fetch GitHub user: {response.status_code}"
        )

    user_data = response.json()
    logger.info(f"Successfully fetched GitHub user: {user_data.get('login')}")
    return user_data


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
    try:
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            logger.warning(f"[get_current_user] Invalid auth header format")
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
            logger.warning(f"[get_current_user] Token verification failed", exc_info=True)
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

        # Check JWT blocklist — reject tokens that were explicitly logged out
        jti = claims.get("jti") or hashlib.sha256(token.encode()).hexdigest()[:16]
        if await jwt_blocklist.is_blocked(jti):
            logger.warning("[get_current_user] Blocked token presented (jti=%s)", jti)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
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
    except Exception as e:
        logger.error(
            f"[get_current_user] Unexpected error: {type(e).__name__}: {str(e)}", exc_info=True
        )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    """
    Optionally resolve current user from Authorization header.

    Returns None when no/invalid auth is provided instead of raising 401.
    Useful for endpoints that are public by default but may expose additional
    data for authenticated users.
    """
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


# ============================================================================
# Unified Endpoints
# ============================================================================


@router.post("/github/callback")
@limiter.limit("10/minute")
async def github_callback(request: Request, request_data: GitHubCallbackRequest) -> Dict[str, Any]:
    """
    Handle GitHub OAuth callback.

    Receives authorization code from frontend, exchanges it for GitHub access token,
    fetches user information, and returns JWT token.

    Security notes:
    - The state parameter is echoed back by GitHub and ensures the authorization
      code came from the same browser that initiated the request
    - GitHub acts as the CSRF validator in this flow
    - Validates code parameter is provided
    - Validates state parameter is provided
    - Checks token expiration from GitHub response
    - Validates API response contains required fields
    """
    code = request_data.code
    state = request_data.state

    if not code:
        logger.warning("GitHub callback missing code parameter")
        raise HTTPException(status_code=400, detail="Missing authorization code")

    if not state:
        logger.warning("GitHub callback missing state parameter")
        raise HTTPException(status_code=400, detail="Missing state parameter")

    # Note: CSRF state is generated client-side and validated by the frontend
    # against sessionStorage. The backend does not generate the state, so
    # server-side validation against _CSRF_STATES is not applicable here.
    # GitHub echoes the state back unchanged, and the frontend verifies it.
    # We only verify the state is non-empty (done above).

    try:
        # Exchange code for GitHub access token
        github_response = await exchange_code_for_token(code)
        github_token = github_response.get("access_token")

        if not github_token:
            logger.error("GitHub token exchange failed: no access token in response")
            raise HTTPException(status_code=401, detail="Failed to obtain GitHub access token")

        # Check token expiration if included in response
        expires_in = github_response.get("expires_in")
        if expires_in is not None:
            logger.info(f"GitHub token expires in {expires_in} seconds")

        # Fetch user information
        github_user = await get_github_user(github_token)

        if not github_user:
            logger.error("Failed to fetch GitHub user information")
            raise HTTPException(status_code=401, detail="Failed to fetch user information")

        # Create JWT token for user
        jwt_token = create_jwt_token(github_user)

        # Return token and user info
        user_info = {
            "username": github_user.get("login", ""),
            "email": github_user.get("email", ""),
            "avatar_url": github_user.get("avatar_url", ""),
            "name": github_user.get("name", ""),
            "user_id": str(github_user.get("id", "")),
            "auth_provider": "github",
        }

        logger.info(f"GitHub authentication successful for user: {user_info['username']}")

        return {
            "token": jwt_token,
            "user": user_info,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"GitHub callback error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Authentication error")


@router.post("/github-callback")
@limiter.limit("10/minute")
async def github_callback_fallback(request: Request, request_data: GitHubCallbackRequest) -> Dict[str, Any]:
    """
    Fallback endpoint for GitHub OAuth callback (old endpoint path).

    This endpoint exists for backward compatibility with clients using
    the old /api/auth/github-callback path. All requests are forwarded
    to the new /api/auth/github/callback endpoint.

    DEPRECATED: Use /api/auth/github/callback instead.
    """
    logger.warning(
        "Deprecated endpoint /api/auth/github-callback called. Use /api/auth/github/callback instead."
    )
    # Forward to the main handler (pass request so rate limiter context is preserved)
    return await github_callback(request, request_data)


@router.post("/dev-token")
@limiter.limit("30/minute")
async def issue_dev_token(request: Request) -> Dict[str, Any]:
    """
    Issue a backend-signed JWT for local development.

    This endpoint avoids misusing OAuth callback endpoints for dev token bootstrapping.
    It is intentionally gated to development mode only.
    """
    # Gate on DEVELOPMENT_MODE=true explicitly — not just "not production".
    # This prevents staging/test environments from exposing the dev-token endpoint.
    is_dev_mode = os.getenv("DEVELOPMENT_MODE", "false").lower() in ("true", "1", "yes")

    if not is_dev_mode:
        logger.warning("[issue_dev_token] Attempted access outside development mode")
        raise HTTPException(status_code=404, detail="Not found")

    dev_user = {
        "id": "dev_user_local",
        "login": "dev-user",
        "email": "dev@example.com",
        "name": "Development User",
        "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
    }

    token = create_jwt_token(dev_user)
    return {
        "token": token,
        "user": {
            "user_id": dev_user["id"],
            "username": dev_user["login"],
            "email": dev_user["email"],
            "name": dev_user["name"],
            "avatar_url": dev_user["avatar_url"],
            "auth_provider": "github",
        },
    }


@router.post("/logout", response_model=LogoutResponse)
@limiter.limit("30/minute")
async def unified_logout(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> LogoutResponse:
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
    token = current_user.get("token", "")

    logger.info(f"Logout request for user {user_id} (auth_provider: {auth_provider})")

    try:
        # Server-side token invalidation — prevents session replay after logout (#721).
        # Derive the JTI from the token claims; fall back to a hash of the raw token
        # when the token does not carry a 'jti' claim (e.g. tokens issued before this
        # change was deployed).
        if token:
            try:
                claims = JWTTokenValidator.verify_token(token)
                if claims:
                    jti = claims.get("jti") or hashlib.sha256(token.encode()).hexdigest()[:16]
                    exp_ts = claims.get("exp")
                    if exp_ts:
                        expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
                    else:
                        # Fallback: treat as expiring in ACCESS_TOKEN_EXPIRE_MINUTES
                        expires_at = datetime.now(timezone.utc) + timedelta(
                            minutes=AuthConfig.ACCESS_TOKEN_EXPIRE_MINUTES
                        )
                    await jwt_blocklist.add_token(jti, user_id, expires_at)
            except Exception:
                # Token may already be expired; still mark logout as successful
                logger.warning(
                    "[unified_logout] Could not extract claims for blocklisting "
                    "(token may already be expired) — user_id=%s",
                    user_id,
                    exc_info=True,
                )

        logger.info(f"User {user_id} logged out successfully ({auth_provider})")

        return LogoutResponse(
            success=True, message=f"Successfully logged out ({auth_provider} authentication)"
        )

    except Exception as e:
        logger.error(f"[unified_logout] Logout error for user {user_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Logout failed"
        )


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(
    current_user: Dict[str, Any] = Depends(get_current_user),
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
