"""
GitHub OAuth Authentication Routes

This module provides OAuth 2.0 authentication endpoints for GitHub login.
It handles code exchange, token generation, session verification, and logout.

DEPENDENCIES:
    pip install python-jose httpx

Routes:
    POST /api/auth/github-callback - Exchange GitHub code for JWT token
    GET /api/auth/verify - Verify JWT token and return user info
    POST /api/auth/logout - Logout and clear session
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Union

import httpx
from fastapi import APIRouter, HTTPException, Depends, Header

# Import JWT handling - requires: pip install python-jose
try:
    from jose import JWTError, jwt
except ImportError as e:
    raise ImportError(
        "python-jose is required for OAuth authentication. "
        "Install it with: pip install python-jose"
    ) from e

logger = logging.getLogger(__name__)

# Configuration from environment
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24

# Router
router = APIRouter(prefix="/api/auth", tags=["auth"])


async def get_github_user(access_token: str) -> Dict[str, Any]:
    """
    Fetch GitHub user information using access token.

    Args:
        access_token: GitHub OAuth access token

    Returns:
        Dictionary with user info (login, id, avatar_url, email, name)

    Raises:
        HTTPException: If GitHub API call fails
    """
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


async def exchange_code_for_token(code: str) -> str:
    """
    Exchange GitHub authorization code for access token.

    Args:
        code: GitHub authorization code from redirect

    Returns:
        GitHub access token

    Raises:
        HTTPException: If code exchange fails
    """
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


def create_jwt_token(user_data: Dict[str, Any]) -> str:
    """
    Create JWT token for authenticated user.

    Args:
        user_data: User information dictionary

    Returns:
        Encoded JWT token
    """
    expiry = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS)

    payload = {
        "sub": user_data.get("login", ""),
        "user_id": user_data.get("id", ""),
        "email": user_data.get("email", ""),
        "avatar_url": user_data.get("avatar_url", ""),
        "name": user_data.get("name", ""),
        "exp": expiry,
        "iat": datetime.utcnow(),
    }

    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"Created JWT token for user: {payload.get('sub')}")

    return token


def verify_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify JWT token and extract payload.

    Args:
        token: JWT token string

    Returns:
        Token payload if valid, None otherwise
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"Invalid JWT token: {str(e)}")
        return None


async def get_token_from_header(authorization: Optional[str] = Header(None)) -> str:
    """
    Extract JWT token from Authorization header.

    Args:
        authorization: Authorization header value

    Returns:
        JWT token string

    Raises:
        HTTPException: If token is missing or invalid format
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    parts = authorization.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    return parts[1]


@router.post("/github-callback")
async def github_callback(request_data: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle GitHub OAuth callback.

    Receives authorization code from frontend, exchanges it for GitHub access token,
    fetches user information, and returns JWT token.

    Request Body:
        - code (str): GitHub authorization code
        - state (str): CSRF state token for validation

    Returns:
        - token (str): JWT token for API authentication
        - user (dict): User information (username, email, avatar_url, name)

    Raises:
        HTTPException: If authentication fails or code/state is invalid
    """
    code = request_data.get("code", "")
    state = request_data.get("state", "")

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
            "user_id": github_user.get("id", ""),
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


@router.get("/verify")
async def verify_session(token: str = Depends(get_token_from_header)) -> Dict[str, Any]:
    """
    Verify JWT token and return user information.

    This endpoint is called on app load to verify the session is still valid.

    Headers:
        Authorization: Bearer <JWT token>

    Returns:
        - user (dict): User information if token is valid
        - expires_at (str): ISO format expiry time

    Raises:
        HTTPException: If token is invalid or expired
    """
    payload = verify_jwt_token(token)

    if not payload:
        logger.warning("Token verification failed - invalid token")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_info = {
        "username": payload.get("sub", ""),
        "email": payload.get("email", ""),
        "avatar_url": payload.get("avatar_url", ""),
        "name": payload.get("name", ""),
        "user_id": payload.get("user_id", ""),
    }

    # Get expiry time from payload
    exp_timestamp = payload.get("exp", 0)
    expires_at = datetime.utcfromtimestamp(exp_timestamp).isoformat()

    logger.info(f"Session verified for user: {user_info['username']}")

    return {
        "user": user_info,
        "expires_at": expires_at,
    }


# NOTE: POST /logout endpoint moved to routes/auth_unified.py
# (unified endpoint that works for all auth types: JWT, OAuth, GitHub)
# See: routes/auth_unified.py for consolidated implementation
    logger.info(f"User logged out: {username}")

    return {
        "success": True,
        "message": "Logged out successfully",
    }


# Health check for auth service
@router.get("/health")
async def auth_health() -> Dict[str, str]:
    """
    Health check endpoint for authentication service.

    Returns:
        - status (str): "healthy" if service is operational
        - timestamp (str): Current UTC timestamp
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }
