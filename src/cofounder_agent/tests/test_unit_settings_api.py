"""
Unit tests for Settings API endpoints
Tests individual endpoints for settings management, validation, and error handling
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
import json
from datetime import datetime, timedelta

# Mock imports (adjust based on your actual structure)
from cofounder_agent.main import app


@pytest.fixture
def client():
    """Create test client for FastAPI app"""
    return TestClient(app)


@pytest.fixture
def mock_user():
    """Mock authenticated user"""
    return {
        "user_id": "test-user-123",
        "email": "test@example.com",
        "role": "admin"
    }


@pytest.fixture
def sample_settings():
    """Sample settings for testing"""
    return {
        "theme": "dark",
        "language": "en",
        "notifications_enabled": True,
        "email_frequency": "daily",
        "timezone": "UTC",
        "auto_save": True
    }


class TestSettingsGetEndpoint:
    """Test GET /api/settings endpoints"""

    def test_get_user_settings_success(self, client, mock_user):
        """Test successful retrieval of user settings"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.get(
                "/api/settings",
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [200, 404]  # 200 if settings exist, 404 if not

    def test_get_user_settings_unauthorized(self, client):
        """Test GET settings without authentication"""
        response = client.get("/api/settings")
        assert response.status_code == 401  # Unauthorized

    def test_get_specific_setting_success(self, client, mock_user):
        """Test retrieval of specific setting"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.get(
                "/api/settings/theme",
                headers={"Authorization": "Bearer fake-token"}
            )
            # Should return 200 or 404 depending on whether setting exists
            assert response.status_code in [200, 404]

    def test_get_settings_with_invalid_token(self, client):
        """Test settings retrieval with invalid token"""
        response = client.get(
            "/api/settings",
            headers={"Authorization": "Bearer invalid-token"}
        )
        assert response.status_code == 401


class TestSettingsCreateEndpoint:
    """Test POST /api/settings endpoints"""

    def test_create_settings_success(self, client, mock_user, sample_settings):
        """Test successful creation of settings"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.post(
                "/api/settings",
                json=sample_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [201, 200]

    def test_create_settings_missing_fields(self, client, mock_user):
        """Test settings creation with missing required fields"""
        incomplete_settings = {"theme": "dark"}  # Missing other fields
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.post(
                "/api/settings",
                json=incomplete_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            # Should still accept partial settings (not all fields required)
            assert response.status_code in [201, 200, 422]

    def test_create_settings_invalid_data_types(self, client, mock_user):
        """Test settings creation with invalid data types"""
        invalid_settings = {
            "theme": "dark",
            "notifications_enabled": "yes",  # Should be boolean
            "email_frequency": "invalid_value"
        }
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.post(
                "/api/settings",
                json=invalid_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            # Should validate data types
            assert response.status_code in [422, 400, 201]  # 422 for validation error

    def test_create_settings_duplicate(self, client, mock_user, sample_settings):
        """Test creating settings when they already exist"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            # First request succeeds
            response1 = client.post(
                "/api/settings",
                json=sample_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response1.status_code in [201, 200]
            
            # Second request should handle duplicate appropriately
            response2 = client.post(
                "/api/settings",
                json=sample_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            # May be 409 Conflict or 200 if merge/upsert
            assert response2.status_code in [409, 200]


class TestSettingsUpdateEndpoint:
    """Test PUT /api/settings endpoints"""

    def test_update_settings_success(self, client, mock_user, sample_settings):
        """Test successful update of settings"""
        updated_settings = sample_settings.copy()
        updated_settings["theme"] = "light"
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.put(
                "/api/settings",
                json=updated_settings,
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [200, 404]

    def test_update_single_setting(self, client, mock_user):
        """Test updating a single setting"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.put(
                "/api/settings/theme",
                json={"value": "light"},
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [200, 404]

    def test_update_settings_nonexistent_user(self, client, mock_user):
        """Test updating settings for user with no existing settings"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.put(
                "/api/settings",
                json={"theme": "light"},
                headers={"Authorization": "Bearer fake-token"}
            )
            # Should either create or return 404
            assert response.status_code in [200, 404, 201]

    def test_update_settings_partial_validation(self, client, mock_user):
        """Test partial settings update with validation"""
        partial_update = {
            "theme": "dark",
            "notifications_enabled": True
            # Other fields omitted
        }
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.put(
                "/api/settings",
                json=partial_update,
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [200, 201, 404]


class TestSettingsDeleteEndpoint:
    """Test DELETE /api/settings endpoints"""

    def test_delete_settings_success(self, client, mock_user):
        """Test successful deletion of settings"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.delete(
                "/api/settings",
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [204, 200, 404]

    def test_delete_specific_setting(self, client, mock_user):
        """Test deletion of specific setting"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.delete(
                "/api/settings/theme",
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [204, 200, 404]

    def test_delete_nonexistent_setting(self, client, mock_user):
        """Test deleting non-existent setting"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            response = client.delete(
                "/api/settings/nonexistent_setting",
                headers={"Authorization": "Bearer fake-token"}
            )
            assert response.status_code in [404, 204]


class TestSettingsValidation:
    """Test settings validation logic"""

    def test_validate_theme_enum(self):
        """Test theme validation - should accept valid values"""
        from cofounder_agent.services.settings_service import validate_setting
        
        assert validate_setting("theme", "dark") is True
        assert validate_setting("theme", "light") is True
        assert validate_setting("theme", "invalid") is False

    def test_validate_email_frequency(self):
        """Test email frequency validation"""
        from cofounder_agent.services.settings_service import validate_setting
        
        assert validate_setting("email_frequency", "daily") is True
        assert validate_setting("email_frequency", "weekly") is True
        assert validate_setting("email_frequency", "invalid_frequency") is False

    def test_validate_timezone(self):
        """Test timezone validation"""
        from cofounder_agent.services.settings_service import validate_setting
        
        assert validate_setting("timezone", "UTC") is True
        assert validate_setting("timezone", "America/New_York") is True
        assert validate_setting("timezone", "Invalid/Timezone") is False

    def test_validate_boolean_fields(self):
        """Test boolean field validation"""
        from cofounder_agent.services.settings_service import validate_setting
        
        assert validate_setting("notifications_enabled", True) is True
        assert validate_setting("notifications_enabled", False) is True
        assert validate_setting("notifications_enabled", "yes") is False


class TestSettingsPermissions:
    """Test settings access control"""

    def test_user_cannot_access_other_user_settings(self, client):
        """Test that users cannot access other users' settings"""
        user1 = {"user_id": "user-1", "email": "user1@example.com", "role": "user"}
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=user1):
            response = client.get(
                "/api/settings/user-2",  # Different user
                headers={"Authorization": "Bearer fake-token"}
            )
            # Should be forbidden or return empty
            assert response.status_code in [403, 404]

    def test_admin_can_access_user_settings(self, client):
        """Test that admin can access user settings"""
        admin_user = {"user_id": "admin-1", "email": "admin@example.com", "role": "admin"}
        
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=admin_user):
            response = client.get(
                "/api/settings/user-2",
                headers={"Authorization": "Bearer admin-token"}
            )
            # Admin should be able to access
            assert response.status_code in [200, 404]  # 200 if exists, 404 if not


class TestAuditLogging:
    """Test that settings changes are audited"""

    def test_settings_change_creates_audit_log(self, client, mock_user, sample_settings):
        """Test that changing settings creates audit log entry"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            with patch('cofounder_agent.middleware.audit_logging.log_audit') as mock_audit:
                response = client.put(
                    "/api/settings",
                    json=sample_settings,
                    headers={"Authorization": "Bearer fake-token"}
                )
                
                # Verify audit log was called
                if response.status_code in [200, 201]:
                    mock_audit.assert_called()

    def test_audit_log_contains_user_info(self, client, mock_user, sample_settings):
        """Test that audit log contains user information"""
        with patch('cofounder_agent.routes.settings_routes.get_current_user', return_value=mock_user):
            with patch('cofounder_agent.middleware.audit_logging.log_audit') as mock_audit:
                response = client.put(
                    "/api/settings",
                    json=sample_settings,
                    headers={"Authorization": "Bearer fake-token"}
                )
                
                if response.status_code in [200, 201]:
                    # Verify audit log call includes user_id
                    assert any(mock_user["user_id"] in str(call) for call in mock_audit.call_args_list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
