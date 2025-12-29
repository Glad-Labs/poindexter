"""Authentication and User Profile Models

Consolidated schemas for authentication and user management.
"""

from pydantic import BaseModel, Field
from typing import Optional


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


class GitHubCallbackRequest(BaseModel):
    """GitHub callback request model."""

    code: str
    state: str
