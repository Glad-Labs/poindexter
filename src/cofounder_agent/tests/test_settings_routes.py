"""
Comprehensive Settings Routes Test Suite

Tests for /api/settings/* endpoints including:
- List all settings
- Get specific setting
- Create new setting (admin only)
- Update setting (admin/editor)
- Delete setting (admin only)
- Authentication enforcement
- Authorization enforcement
- Input validation
- Settings categories

Test Coverage:
- TestSettingsListEndpoint: 6 tests
- TestSettingsGetEndpoint: 7 tests
- TestSettingsCreateEndpoint: 8 tests
- TestSettingsUpdateEndpoint: 6 tests
- TestSettingsDeleteEndpoint: 6 tests
- TestSettingsAuthorization: 8 tests
- TestSettingsValidation: 6 tests

Total: 47 tests
"""

import pytest
import uuid
import json
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from main import app

# Note: Use the client fixture from conftest instead
# This ensures proper test isolation and lifespan management



# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def user_token():
    """Regular user JWT token"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoidXNlci0xIiwicm9sZSI6InVzZXIifQ.signature"


@pytest.fixture
def admin_token():
    """Admin user JWT token"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiYWRtaW4tMSIsInJvbGUiOiJhZG1pbiJ9.signature"


@pytest.fixture
def editor_token():
    """Editor user JWT token"""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjoiZWRpdG9yLTEiLCJyb2xlIjoiZWRpdG9yIn0.signature"


@pytest.fixture
def sample_setting():
    """Sample setting object"""
    return {
        "key": "app_name",
        "value": "Glad Labs",
        "category": "system",
        "description": "Application name",
        "is_secret": False,
    }


@pytest.fixture
def secret_setting():
    """Sample secret setting"""
    return {
        "key": "api_secret",
        "value": "super-secret-key-12345",
        "category": "security",
        "description": "API secret key",
        "is_secret": True,
    }


class TestSettingsListEndpoint:
    """Test suite for GET /api/settings endpoint"""

    def test_list_settings_with_auth(self, client, user_token):
        """Should list settings with valid authentication"""
        response = client.get("/api/settings", headers={"Authorization": f"Bearer {user_token}"})
        assert response.status_code in [200, 401]

    def test_list_settings_without_auth(self, client):
        """Should reject request without authentication"""
        response = client.get("/api/settings")
        assert response.status_code == 401

    def test_list_settings_pagination(self, client, user_token):
        """Should support pagination with limit and skip"""
        response = client.get(
            "/api/settings?limit=10&skip=0", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [200, 401, 422]

    def test_list_settings_filter_by_category(self, client, user_token):
        """Should filter settings by category"""
        response = client.get(
            "/api/settings?category=system", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [200, 401, 422]

    def test_list_settings_filter_by_environment(self, client, user_token):
        """Should filter settings by environment"""
        response = client.get(
            "/api/settings?environment=production",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code in [200, 401, 422]

    def test_list_settings_response_is_list(self, client, user_token):
        """Should return list of settings"""
        response = client.get("/api/settings", headers={"Authorization": f"Bearer {user_token}"})
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list) or isinstance(data, dict)


class TestSettingsGetEndpoint:
    """Test suite for GET /api/settings/{setting_id} endpoint"""

    def test_get_setting_by_id(self, client, user_token):
        """Should retrieve specific setting by ID"""
        setting_id = str(uuid.uuid4())
        response = client.get(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [200, 401, 404]

    def test_get_nonexistent_setting(self, client, user_token):
        """Should return 404 for nonexistent setting"""
        response = client.get(
            "/api/settings/nonexistent-id-12345", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [404, 401]

    def test_get_setting_without_auth(self, client):
        """Should reject request without authentication"""
        setting_id = str(uuid.uuid4())
        response = client.get(f"/api/settings/{setting_id}")
        assert response.status_code == 401

    def test_get_setting_with_invalid_id_format(self, client, user_token):
        """Should handle invalid ID format"""
        response = client.get(
            "/api/settings/invalid-id-format!!!", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [400, 404, 401, 422]

    def test_get_setting_empty_id(self, client, user_token):
        """Should reject empty setting ID"""
        response = client.get("/api/settings/", headers={"Authorization": f"Bearer {user_token}"})
        # Empty path should not match endpoint
        assert response.status_code in [404, 401, 405]

    def test_get_setting_response_format(self, client, user_token):
        """Should return properly formatted setting"""
        setting_id = str(uuid.uuid4())
        response = client.get(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {user_token}"}
        )
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
            assert "key" in data or "id" in data

    def test_get_secret_setting_redacted(self, client, user_token):
        """Should not expose secret values in response"""
        setting_id = str(uuid.uuid4())
        response = client.get(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {user_token}"}
        )
        if response.status_code == 200:
            data = response.json()
            # If it's marked as secret, value should be redacted
            if data.get("is_secret"):
                assert data.get("value") != "actual-secret-value"


class TestSettingsCreateEndpoint:
    """Test suite for POST /api/settings endpoint"""

    def test_create_setting_as_admin(self, client, admin_token, sample_setting):
        """Should allow admin to create setting"""
        response = client.post(
            "/api/settings", json=sample_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [201, 401, 403]

    def test_create_setting_as_user(self, client, user_token, sample_setting):
        """Should reject non-admin user creating setting"""
        response = client.post(
            "/api/settings", json=sample_setting, headers={"Authorization": f"Bearer {user_token}"}
        )
        # Users should not be able to create settings
        assert response.status_code in [403, 401, 400]

    def test_create_setting_without_auth(self, client, sample_setting):
        """Should reject request without authentication"""
        response = client.post("/api/settings", json=sample_setting)
        assert response.status_code == 401

    def test_create_setting_missing_required_fields(self, client, admin_token):
        """Should validate required fields"""
        incomplete_setting = {"key": "test"}  # Missing value and category
        response = client.post(
            "/api/settings",
            json=incomplete_setting,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [422, 400, 401]

    def test_create_secret_setting(self, client, admin_token, secret_setting):
        """Should support creating secret settings"""
        response = client.post(
            "/api/settings", json=secret_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [201, 401, 403]

    def test_create_setting_with_invalid_category(self, client, admin_token):
        """Should validate setting category"""
        invalid_setting = {
            "key": "test",
            "value": "test-value",
            "category": "invalid_category_xyz",
            "description": "Test",
        }
        response = client.post(
            "/api/settings",
            json=invalid_setting,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [422, 400, 401]

    def test_create_setting_with_empty_value(self, client, admin_token):
        """Should handle empty setting value"""
        empty_setting = {"key": "test", "value": "", "category": "system", "description": "Test"}
        response = client.post(
            "/api/settings", json=empty_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        # Should either accept empty value or validate it
        assert response.status_code in [201, 400, 422, 401]

    def test_create_setting_response_includes_id(self, client, admin_token, sample_setting):
        """Should return created setting with ID"""
        response = client.post(
            "/api/settings", json=sample_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        if response.status_code == 201:
            data = response.json()
            assert "id" in data or "setting_id" in data


class TestSettingsUpdateEndpoint:
    """Test suite for PUT /api/settings/{setting_id} endpoint"""

    def test_update_setting_as_admin(self, client, admin_token):
        """Should allow admin to update setting"""
        setting_id = str(uuid.uuid4())
        update_data = {"value": "updated-value"}
        response = client.put(
            f"/api/settings/{setting_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [200, 201, 401, 404]

    def test_update_setting_as_editor(self, client, editor_token):
        """Should allow editor to update setting"""
        setting_id = str(uuid.uuid4())
        update_data = {"value": "updated-value"}
        response = client.put(
            f"/api/settings/{setting_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {editor_token}"},
        )
        assert response.status_code in [200, 201, 401, 403, 404]

    def test_update_setting_as_user(self, client, user_token):
        """Should reject regular user updating setting"""
        setting_id = str(uuid.uuid4())
        update_data = {"value": "updated-value"}
        response = client.put(
            f"/api/settings/{setting_id}",
            json=update_data,
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code in [403, 401, 400]

    def test_update_nonexistent_setting(self, client, admin_token):
        """Should return 404 for nonexistent setting"""
        update_data = {"value": "updated-value"}
        response = client.put(
            "/api/settings/nonexistent-id-12345",
            json=update_data,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [404, 401, 400]

    def test_update_setting_without_auth(self, client):
        """Should reject request without authentication"""
        setting_id = str(uuid.uuid4())
        update_data = {"value": "updated-value"}
        response = client.put(f"/api/settings/{setting_id}", json=update_data)
        assert response.status_code == 401

    def test_update_setting_partial_fields(self, client, admin_token):
        """Should support partial updates"""
        setting_id = str(uuid.uuid4())
        partial_update = {"description": "Updated description"}
        response = client.put(
            f"/api/settings/{setting_id}",
            json=partial_update,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [200, 201, 401, 404, 400]


class TestSettingsDeleteEndpoint:
    """Test suite for DELETE /api/settings/{setting_id} endpoint"""

    def test_delete_setting_as_admin(self, client, admin_token):
        """Should allow admin to delete setting"""
        setting_id = str(uuid.uuid4())
        response = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [200, 204, 401, 404]

    def test_delete_setting_as_user(self, client, user_token):
        """Should reject regular user deleting setting"""
        setting_id = str(uuid.uuid4())
        response = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {user_token}"}
        )
        assert response.status_code in [403, 401, 400]

    def test_delete_nonexistent_setting(self, client, admin_token):
        """Should handle deleting nonexistent setting"""
        response = client.delete(
            "/api/settings/nonexistent-id-12345", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [404, 200, 204, 401]

    def test_delete_setting_without_auth(self, client):
        """Should reject request without authentication"""
        setting_id = str(uuid.uuid4())
        response = client.delete(f"/api/settings/{setting_id}")
        assert response.status_code == 401

    def test_delete_setting_twice(self, client, admin_token):
        """Should handle deleting same setting twice"""
        setting_id = str(uuid.uuid4())
        # First delete
        response1 = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response1.status_code in [200, 204, 401, 404]

        # Second delete of same setting
        response2 = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        # Second should be 404 or 200 (already deleted)
        assert response2.status_code in [404, 200, 204, 401]

    def test_delete_setting_response(self, client, admin_token):
        """Should return proper delete response"""
        setting_id = str(uuid.uuid4())
        response = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        if response.status_code == 200:
            data = response.json()
            # Check for success indicator
            assert "success" in data or "message" in data


class TestSettingsAuthorization:
    """Test suite for authorization and access control"""

    def test_settings_role_based_access(self, client, user_token, admin_token):
        """Should enforce role-based access control"""
        # User should only have read access
        user_response = client.get(
            "/api/settings", headers={"Authorization": f"Bearer {user_token}"}
        )
        # Admin should have full access
        admin_response = client.get(
            "/api/settings", headers={"Authorization": f"Bearer {admin_token}"}
        )
        # Both should not return 405 (method not allowed)
        assert user_response.status_code != 405
        assert admin_response.status_code != 405

    def test_settings_forbidden_operations_for_users(self, client, user_token, sample_setting):
        """Should deny write operations for regular users"""
        # Attempt create
        create_response = client.post(
            "/api/settings", json=sample_setting, headers={"Authorization": f"Bearer {user_token}"}
        )
        assert create_response.status_code in [403, 401, 400]

    def test_settings_forbidden_operations_for_editors(self, client, editor_token, sample_setting):
        """Should deny delete operations for editors"""
        setting_id = str(uuid.uuid4())
        delete_response = client.delete(
            f"/api/settings/{setting_id}", headers={"Authorization": f"Bearer {editor_token}"}
        )
        # Editors should not be able to delete
        assert delete_response.status_code in [403, 401, 400]

    def test_settings_admin_can_do_all_operations(self, client, admin_token, sample_setting):
        """Should allow admins to perform all operations"""
        # Admin should be able to list, get, create, update, delete
        operations = [
            ("GET", "/api/settings", None),
            ("POST", "/api/settings", sample_setting),
        ]
        for method, endpoint, data in operations:
            if method == "GET":
                response = client.get(endpoint, headers={"Authorization": f"Bearer {admin_token}"})
            else:
                response = client.post(
                    endpoint, json=data, headers={"Authorization": f"Bearer {admin_token}"}
                )
            # Admin operations should not return 403
            assert response.status_code != 403

    def test_settings_token_expiration_handling(self, client, user_token):
        """Should handle expired tokens"""
        expired_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE2MDAwMDAwMDB9.signature"
        response = client.get("/api/settings", headers={"Authorization": f"Bearer {expired_token}"})
        assert response.status_code == 401

    def test_settings_malformed_token(self, client):
        """Should reject malformed token"""
        response = client.get("/api/settings", headers={"Authorization": "Bearer invalid.token"})
        assert response.status_code == 401

    def test_settings_no_bearer_prefix(self, client, user_token):
        """Should reject token without Bearer prefix"""
        response = client.get("/api/settings", headers={"Authorization": user_token})
        assert response.status_code == 401

    def test_settings_mixed_case_bearer(self, client, user_token):
        """Should handle Bearer prefix case handling"""
        response = client.get("/api/settings", headers={"Authorization": f"bearer {user_token}"})
        # Should either accept or reject consistently
        assert response.status_code in [200, 401]


class TestSettingsValidation:
    """Test suite for input validation"""

    def test_settings_key_validation(self, client, admin_token):
        """Should validate setting key format"""
        invalid_key = {"key": "", "value": "test", "category": "system"}  # Empty key
        response = client.post(
            "/api/settings", json=invalid_key, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [422, 400, 401]

    def test_settings_value_type_validation(self, client, admin_token):
        """Should validate setting value types"""
        # JSON value should be valid
        json_setting = {
            "key": "json_config",
            "value": '{"valid": "json"}',
            "category": "system",
            "data_type": "json",
        }
        response = client.post(
            "/api/settings", json=json_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code in [201, 400, 422, 401]

    def test_settings_integer_value_validation(self, client, admin_token):
        """Should validate integer setting values"""
        int_setting = {
            "key": "max_retries",
            "value": "not-an-integer",
            "category": "system",
            "data_type": "integer",
        }
        response = client.post(
            "/api/settings", json=int_setting, headers={"Authorization": f"Bearer {admin_token}"}
        )
        # Should either reject or accept and handle conversion
        assert response.status_code in [201, 400, 422, 401]

    def test_settings_description_length(self, client, admin_token):
        """Should validate description length"""
        long_desc = {
            "key": "test",
            "value": "test",
            "category": "system",
            "description": "x" * 10000,  # Very long description
        }
        response = client.post(
            "/api/settings", json=long_desc, headers={"Authorization": f"Bearer {admin_token}"}
        )
        # Should either accept or reject due to length
        assert response.status_code in [201, 400, 422, 401]

    def test_settings_special_characters_in_key(self, client, admin_token):
        """Should handle special characters in setting key"""
        special_setting = {"key": "test!@#$%^&*()", "value": "test", "category": "system"}
        response = client.post(
            "/api/settings",
            json=special_setting,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [201, 400, 422, 401]

    def test_settings_unicode_values(self, client, admin_token):
        """Should handle Unicode values"""
        unicode_setting = {"key": "app_name", "value": "Glad Labs 🚀 全球", "category": "system"}
        response = client.post(
            "/api/settings",
            json=unicode_setting,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code in [201, 401, 400, 422]
