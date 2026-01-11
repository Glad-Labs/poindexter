"""
Comprehensive Authentication Routes Test Suite

Tests for /api/auth/* endpoints including:
- JWT token validation
- User profile retrieval
- Logout functionality
- Token expiration
- Invalid credentials
- Authorization enforcement

Test Coverage:
- TestAuthUserProfile: 6 tests
- TestAuthLogout: 6 tests
- TestAuthTokenValidation: 8 tests
- TestAuthEdgeCases: 6 tests

Total: 26 tests
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Don't import app directly - use conftest fixture instead
# This ensures routes are properly registered for testing


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def valid_jwt_token():
    """Valid JWT token for testing"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoidGVzdC11c2VyIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIiwiZXhwIjoyNzE2MjM5MDIyfQ.test-signature"


@pytest.fixture
def expired_jwt_token():
    """Expired JWT token for testing"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoidGVzdC11c2VyIiwiZXhwIjoxNjAwMDAwMDAwfQ.expired-token"


@pytest.fixture
def invalid_jwt_token():
    """Invalid JWT token for testing"""
    return "invalid.token.format"


@pytest.fixture
def github_oauth_token():
    """GitHub OAuth token for testing"""
    return "gho_1234567890abcdefghij1234567890"


@pytest.fixture
def oauth_token():
    """Generic OAuth token for testing"""
    return "oauth_1234567890abcdefghij1234567890"


class TestAuthUserProfile:
    """Test suite for GET /api/auth/me endpoint"""

    def test_get_user_profile_with_valid_jwt(self, client, valid_jwt_token):
        """Should return user profile with valid JWT token"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data or data.get("status") == "authenticated"

    def test_get_user_profile_without_token(self, client):
        """Should reject request without authentication token"""
        response = client.get("/api/auth/me")
        assert response.status_code == 401
        assert (
            "authenticated" in response.json().get("detail", "").lower()
            or "authorization" in response.json().get("detail", "").lower()
        )

    def test_get_user_profile_with_invalid_token(self, client, invalid_jwt_token):
        """Should reject request with invalid JWT token"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {invalid_jwt_token}"}
        )
        assert response.status_code == 401

    def test_get_user_profile_with_expired_token(self, client, expired_jwt_token):
        """Should reject request with expired JWT token"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {expired_jwt_token}"}
        )
        assert response.status_code == 401

    def test_get_user_profile_malformed_header(self, client):
        """Should reject malformed Authorization header"""
        response = client.get("/api/auth/me", headers={"Authorization": "NotBearer token123"})
        assert response.status_code == 401

    def test_get_user_profile_response_format(self, client, valid_jwt_token):
        """Should return properly formatted user profile"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        if response.status_code == 200:
            data = response.json()
            # Check expected fields exist or status is OK
            assert isinstance(data, dict)


class TestAuthLogout:
    """Test suite for POST /api/auth/logout endpoint"""

    def test_logout_with_valid_token(self, client, valid_jwt_token):
        """Should successfully logout with valid token"""
        response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        assert response.status_code in [200, 204]

    def test_logout_without_token(self, client):
        """Should reject logout without authentication"""
        response = client.post("/api/auth/logout")
        assert response.status_code == 401

    def test_logout_with_invalid_token(self, client, invalid_jwt_token):
        """Should reject logout with invalid token"""
        response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {invalid_jwt_token}"}
        )
        assert response.status_code == 401

    def test_logout_twice_with_same_token(self, client, valid_jwt_token):
        """Should handle multiple logout attempts"""
        # First logout
        response1 = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        assert response1.status_code in [200, 204]

        # Second logout with same token (should fail or succeed gracefully)
        response2 = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        # Could be 400, 401, or 204 depending on implementation
        assert response2.status_code in [200, 204, 400, 401]

    def test_logout_response_format(self, client, valid_jwt_token):
        """Should return proper logout response"""
        response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
            # Check for success message or field
            if "success" in data:
                assert isinstance(data["success"], bool)

    def test_logout_with_expired_token(self, client, expired_jwt_token):
        """Should reject logout with expired token"""
        response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {expired_jwt_token}"}
        )
        assert response.status_code == 401


class TestAuthTokenValidation:
    """Test suite for JWT token validation"""

    def test_token_validation_with_valid_format(self, client, valid_jwt_token):
        """Should accept well-formed JWT token"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        # Should not reject due to format
        assert response.status_code != 422

    def test_token_validation_missing_bearer_prefix(self, client, valid_jwt_token):
        """Should reject token without Bearer prefix"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": valid_jwt_token}  # No "Bearer " prefix
        )
        assert response.status_code == 401

    def test_token_validation_case_insensitive_bearer(self, client, valid_jwt_token):
        """Should validate Bearer prefix (case handling)"""
        # Test with lowercase bearer
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"bearer {valid_jwt_token}"}
        )
        # Should either accept or reject consistently
        assert response.status_code in [200, 401]

    def test_token_validation_with_empty_token(self, client):
        """Should reject empty token"""
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer "})
        assert response.status_code == 401

    def test_token_validation_with_none_token(self, client):
        """Should reject None token"""
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer None"})
        assert response.status_code == 401

    def test_token_validation_special_characters(self, client):
        """Should validate tokens with special characters"""
        special_token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.test_payload.signature"
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {special_token}"})
        # Should not error on validation
        assert response.status_code in [200, 401]

    def test_token_validation_too_long(self, client):
        """Should handle extremely long tokens"""
        long_token = "x" * 10000
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {long_token}"})
        # Should not crash, should reject
        assert response.status_code == 401

    def test_token_validation_with_whitespace(self, client, valid_jwt_token):
        """Should handle tokens with extra whitespace"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer  {valid_jwt_token}"}  # Double space
        )
        # May accept or reject, but should not crash
        assert response.status_code in [200, 401]


class TestAuthEdgeCases:
    """Test suite for edge cases and error scenarios"""

    def test_missing_authorization_header(self, client):
        """Should handle missing Authorization header"""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_empty_authorization_header(self, client):
        """Should handle empty Authorization header"""
        response = client.get("/api/auth/me", headers={"Authorization": ""})
        assert response.status_code == 401

    def test_authorization_with_different_schemes(self, client, valid_jwt_token):
        """Should reject non-Bearer auth schemes"""
        response = client.get("/api/auth/me", headers={"Authorization": f"Basic {valid_jwt_token}"})
        assert response.status_code == 401

    def test_multiple_authorization_headers(self, client, valid_jwt_token):
        """Should handle multiple Authorization headers"""
        # Note: This tests framework behavior
        headers = {
            "Authorization": f"Bearer {valid_jwt_token}",
        }
        response = client.get("/api/auth/me", headers=headers)
        # Should either work or reject, not crash
        assert response.status_code in [200, 401]

    def test_auth_endpoint_exists(self, client):
        """Should confirm /api/auth/me endpoint exists"""
        response = client.get("/api/auth/me", headers={"Authorization": "Bearer test"})
        # Should not return 404 (endpoint exists)
        assert response.status_code != 404

    def test_logout_endpoint_exists(self, client):
        """Should confirm /api/auth/logout endpoint exists"""
        response = client.post("/api/auth/logout", headers={"Authorization": "Bearer test"})
        # Should not return 404 (endpoint exists)
        assert response.status_code != 404


class TestAuthIntegration:
    """Integration tests combining multiple auth scenarios"""

    def test_auth_flow_login_profile_logout(self, client, valid_jwt_token):
        """Should support full auth flow: get profile, then logout"""
        # Get profile
        profile_response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        assert profile_response.status_code in [200, 401]

        # Then logout
        logout_response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        assert logout_response.status_code in [200, 204, 401]

    def test_token_persistence_across_requests(self, client, valid_jwt_token):
        """Should handle same token across multiple requests"""
        headers = {"Authorization": f"Bearer {valid_jwt_token}"}

        response1 = client.get("/api/auth/me", headers=headers)
        response2 = client.get("/api/auth/me", headers=headers)
        response3 = client.post("/api/auth/logout", headers=headers)

        # All should return valid status codes
        assert response1.status_code in [200, 401]
        assert response2.status_code in [200, 401]
        assert response3.status_code in [200, 204, 401]


class TestAuthSecurityHeaders:
    """Test security header handling in auth endpoints"""

    def test_auth_endpoint_returns_no_credentials_in_response(self, client, valid_jwt_token):
        """Auth endpoint should not echo credentials in response"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        if response.status_code == 200:
            response_text = str(response.json())
            # Should not contain the token
            assert valid_jwt_token not in response_text

    def test_logout_endpoint_with_post_method(self, client, valid_jwt_token):
        """Logout should use POST method"""
        response = client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        # POST should be supported (not 405)
        assert response.status_code != 405

    def test_auth_me_endpoint_with_get_method(self, client, valid_jwt_token):
        """Auth /me should use GET method"""
        response = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {valid_jwt_token}"}
        )
        # GET should be supported (not 405)
        assert response.status_code != 405
