"""
Unit tests for auth_schemas.py

Tests field validation and model behaviour for authentication schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.auth_schemas import GitHubCallbackRequest, LogoutResponse, UserProfile


@pytest.mark.unit
class TestUserProfile:
    def _valid(self, **kwargs):
        defaults = {
            "id": "user-123",
            "email": "test@example.com",
            "username": "testuser",
            "auth_provider": "github",
            "is_active": True,
            "created_at": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        return UserProfile(**defaults)

    def test_valid(self):
        profile = self._valid()
        assert profile.id == "user-123"
        assert profile.email == "test@example.com"
        assert profile.auth_provider == "github"
        assert profile.is_active is True

    def test_jwt_provider(self):
        profile = self._valid(auth_provider="jwt")
        assert profile.auth_provider == "jwt"

    def test_oauth_provider(self):
        profile = self._valid(auth_provider="oauth")
        assert profile.auth_provider == "oauth"

    def test_inactive_user(self):
        profile = self._valid(is_active=False)
        assert profile.is_active is False

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            UserProfile(  # type: ignore[call-arg]
                id="user-123",
                email="test@example.com",
                # missing username
                auth_provider="github",
                is_active=True,
                created_at="2026-01-01T00:00:00Z",
            )


@pytest.mark.unit
class TestLogoutResponse:
    def test_successful_logout(self):
        resp = LogoutResponse(success=True, message="Logged out successfully")
        assert resp.success is True
        assert "Logged out" in resp.message

    def test_failed_logout(self):
        resp = LogoutResponse(success=False, message="Token already expired")
        assert resp.success is False

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            LogoutResponse(success=True)  # type: ignore[call-arg]


@pytest.mark.unit
class TestGitHubCallbackRequest:
    def test_valid(self):
        req = GitHubCallbackRequest(code="abc123", state="state-xyz")
        assert req.code == "abc123"
        assert req.state == "state-xyz"

    def test_missing_code_raises(self):
        with pytest.raises(ValidationError):
            GitHubCallbackRequest(state="state-xyz")  # type: ignore[call-arg]

    def test_missing_state_raises(self):
        with pytest.raises(ValidationError):
            GitHubCallbackRequest(code="abc123")  # type: ignore[call-arg]
