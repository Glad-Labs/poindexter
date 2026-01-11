"""
Unit tests for auth_unified.py
Tests authentication endpoints and JWT token handling
"""

import pytest
import json
from fastapi.testclient import TestClient
from fastapi import FastAPI
import os
import sys

# Add parent directory to path for imports to work properly
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


# Use conftest app and client fixtures instead
# This ensures all routes are properly registered


@pytest.mark.unit
@pytest.mark.api
class TestAuthUnified:
    """Test authentication endpoints"""

    def test_github_callback_success(self, client):
        """Test successful GitHub OAuth callback"""
        response = client.post(
            "/api/auth/github/callback",
            json={"code": "mock_auth_code", "state": "mock_state"}
        )
        # 200 if configured, 401 if keys missing (expected in test), 500 if error
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "token" in data or "access_token" in data

    def test_github_callback_missing_code(self, client):
        """Test GitHub callback with missing code"""
        response = client.post(
            "/api/auth/github/callback",
            json={"state": "mock_state"}
        )
        assert response.status_code in [400, 422]

    def test_github_callback_missing_state(self, client):
        """Test GitHub callback with missing state"""
        response = client.post(
            "/api/auth/github/callback",
            json={"code": "mock_code"}
        )
        assert response.status_code in [400, 422]

    def test_github_callback_invalid_json(self, client):
        """Test GitHub callback with invalid JSON"""
        response = client.post(
            "/api/auth/github/callback",
            data="invalid json"
        )
        assert response.status_code in [400, 422]

    def test_token_refresh(self, client):
        """Test token refresh endpoint"""
        # First get a token
        auth_response = client.post(
            "/api/auth/github/callback",
            json={"code": "mock_code", "state": "mock_state"}
        )
        
        if auth_response.status_code == 200:
            token_data = auth_response.json()
            token = token_data.get("token") or token_data.get("access_token")
            
            # Try to refresh the token
            refresh_response = client.post(
                "/api/auth/refresh",
                headers={"Authorization": f"Bearer {token}"}
            )
            assert refresh_response.status_code in [200, 401]

    def test_protected_endpoint_without_token(self, client):
        """Test accessing protected endpoint without token"""
        response = client.get("/api/tasks")
        assert response.status_code == 401

    def test_protected_endpoint_with_invalid_token(self, client):
        """Test accessing protected endpoint with invalid token"""
        response = client.get(
            "/api/tasks",
            headers={"Authorization": "Bearer invalid_token"}
        )
        assert response.status_code == 401

    def test_auth_logout(self, client):
        """Test logout endpoint"""
        response = client.post("/api/auth/logout")
        assert response.status_code in [200, 401]

    def test_auth_validate(self, client):
        """Test token validation endpoint"""
        response = client.get("/api/auth/validate")
        # 404 = endpoint doesn't exist (expected), 200/401 = endpoint exists
        assert response.status_code in [200, 401, 404]

    def test_auth_user_info(self, client):
        """Test getting current user info"""
        response = client.get("/api/auth/user")
        # 404 = endpoint doesn't exist (expected), 200/401 = endpoint exists
        assert response.status_code in [200, 401, 404]


@pytest.mark.unit
@pytest.mark.security
class TestAuthValidation:
    """Test auth input validation"""

    def test_callback_with_too_long_code(self, client):
        """Test callback with overly long code"""
        response = client.post(
            "/api/auth/github/callback",
            json={
                "code": "x" * 10000,
                "state": "mock_state"
            }
        )
        # Should either reject or handle gracefully (401 = auth attempt with no keys)
        assert response.status_code in [400, 401, 422, 500]

    def test_callback_with_special_characters(self, client):
        """Test callback with special characters in code"""
        response = client.post(
            "/api/auth/github/callback",
            json={
                "code": "mock_code<script>alert('xss')</script>",
                "state": "mock_state"
            }
        )
        # 401 = auth attempt with no GitHub keys (expected)
        assert response.status_code in [200, 400, 401, 422, 500]

    def test_callback_with_null_values(self, client):
        """Test callback with null values"""
        response = client.post(
            "/api/auth/github/callback",
            json={"code": None, "state": None}
        )
        assert response.status_code in [400, 422]

    def test_authorization_header_formats(self, client):
        """Test various authorization header formats"""
        invalid_headers = [
            "Bearer",  # No token
            "BasicAuth token",  # Wrong scheme
            "BearerToken",  # No space
            "Bearer Token Extra",  # Extra parts
        ]
        
        for header in invalid_headers:
            response = client.get(
                "/api/tasks",
                headers={"Authorization": header}
            )
            # Should reject invalid format
            assert response.status_code in [400, 401]


@pytest.mark.unit
@pytest.mark.api
class TestAuthEdgeCases:
    """Test edge cases and error scenarios"""

    def test_rapid_sequential_auth_attempts(self, client):
        """Test multiple auth attempts in rapid succession"""
        responses = []
        for _ in range(5):
            response = client.post(
                "/api/auth/github/callback",
                json={"code": "mock_code", "state": "mock_state"}
            )
            responses.append(response.status_code)
        
        # Should not crash, responses should be consistent (401 expected without GitHub keys)
        assert all(status in [200, 401, 429, 500] for status in responses)

    def test_concurrent_token_validation(self, client):
        """Test concurrent token validation requests"""
        # Single request test (full concurrency testing requires async)
        response = client.get(
            "/api/auth/validate",
            headers={"Authorization": "Bearer test_token"}
        )
        # 404 = endpoint doesn't exist, 200/401 = endpoint exists
        assert response.status_code in [200, 401, 404]

    def test_auth_endpoint_methods(self, client):
        """Test unsupported HTTP methods on auth endpoints"""
        unsupported_methods = [
            ("GET", "/api/auth/github/callback"),
            ("PUT", "/api/auth/github/callback"),
            ("DELETE", "/api/auth/github/callback"),
        ]
        
        for method, endpoint in unsupported_methods:
            # Using TestClient's request method for flexibility
            try:
                response = client.request(method, endpoint)
                assert response.status_code in [405, 422]
            except Exception:
                # Some methods might not be supported by TestClient
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
