"""
Simple Bearer token authentication for solo operator.

Replaces the JWT + GitHub OAuth system. All API requests must include
Authorization: Bearer <API_TOKEN> header where API_TOKEN matches the
API_TOKEN environment variable.

OpenClaw skills and Grafana alerts use this token.
"""

import os
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


async def verify_api_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """Verify the Bearer token matches API_TOKEN env var.

    Also allows the existing dev-token bypass when DEVELOPMENT_MODE=true.

    Returns:
        The verified token string.
    """
    api_token = os.getenv("API_TOKEN", "")
    dev_mode = os.getenv("DEVELOPMENT_MODE", "").lower() == "true"

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = credentials.credentials

    # Dev mode bypass: only accept the explicit dev-token
    if dev_mode and token == "dev-token":
        return token

    if not api_token:
        raise HTTPException(status_code=500, detail="API_TOKEN not configured")

    import hmac
    if not hmac.compare_digest(token, api_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    return token



# Fixed operator identity for solo-operator mode.
# In a single-operator system, all authenticated requests come from the owner.
OPERATOR_ID = os.getenv("OPERATOR_ID", "operator")


def get_operator_identity() -> dict:
    """Return a fixed operator identity dict for solo-operator mode."""
    return {
        "id": OPERATOR_ID,
        "email": "operator@glad-labs.ai",
        "username": "operator",
        "auth_provider": "api_token",
        "is_active": True,
    }


async def verify_api_token_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Optional[str]:
    """Like verify_api_token but returns None instead of raising 401.

    Used for public endpoints that optionally accept auth (e.g. list_posts
    shows drafts only when authenticated).
    """
    api_token = os.getenv("API_TOKEN", "")
    dev_mode = os.getenv("DEVELOPMENT_MODE", "").lower() == "true"

    if not credentials:
        return None

    token = credentials.credentials

    if dev_mode and token == "dev-token":
        return token

    import hmac
    if not api_token or not hmac.compare_digest(token, api_token):
        return None

    return token
