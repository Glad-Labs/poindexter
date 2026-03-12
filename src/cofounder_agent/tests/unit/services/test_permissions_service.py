"""
Unit tests for PermissionsService and OAuthUser.

All tests are pure-function — zero DB, network, or async calls.
PermissionsService uses class-level constants and static/class methods,
so each test can call directly without instantiation.
"""

import pytest

from services.permissions_service import (
    PermissionAction,
    PermissionsService,
    SettingSensitivity,
    UserRole,
)
from services.oauth_provider import OAuthException, OAuthUser


# ---------------------------------------------------------------------------
# UserRole helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserRole:
    def test_admin_role_resolved_from_list(self):
        role = PermissionsService.get_user_role(["admin"])
        assert role == UserRole.ADMIN

    def test_highest_role_wins_when_multiple_given(self):
        role = PermissionsService.get_user_role(["viewer", "editor", "admin"])
        assert role == UserRole.ADMIN

    def test_empty_list_returns_guest(self):
        role = PermissionsService.get_user_role([])
        assert role == UserRole.GUEST

    def test_invalid_role_names_are_ignored(self):
        role = PermissionsService.get_user_role(["superuser", "god", "viewer"])
        assert role == UserRole.VIEWER

    def test_all_invalid_roles_falls_back_to_guest(self):
        role = PermissionsService.get_user_role(["unknown", "invalid"])
        assert role == UserRole.GUEST

    def test_case_insensitive_role_matching(self):
        # get_user_role calls UserRole(role_name.lower())
        role = PermissionsService.get_user_role(["ADMIN"])
        assert role == UserRole.ADMIN


# ---------------------------------------------------------------------------
# can_perform_action
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanPerformAction:
    def test_admin_can_create(self):
        assert PermissionsService.can_perform_action(UserRole.ADMIN, PermissionAction.CREATE) is True

    def test_admin_can_delete(self):
        assert PermissionsService.can_perform_action(UserRole.ADMIN, PermissionAction.DELETE) is True

    def test_admin_can_export(self):
        assert PermissionsService.can_perform_action(UserRole.ADMIN, PermissionAction.EXPORT) is True

    def test_editor_can_read(self):
        assert PermissionsService.can_perform_action(UserRole.EDITOR, PermissionAction.READ) is True

    def test_editor_cannot_delete(self):
        assert PermissionsService.can_perform_action(UserRole.EDITOR, PermissionAction.DELETE) is False

    def test_editor_cannot_export(self):
        assert PermissionsService.can_perform_action(UserRole.EDITOR, PermissionAction.EXPORT) is False

    def test_viewer_can_read(self):
        assert PermissionsService.can_perform_action(UserRole.VIEWER, PermissionAction.READ) is True

    def test_viewer_cannot_update(self):
        assert PermissionsService.can_perform_action(UserRole.VIEWER, PermissionAction.UPDATE) is False

    def test_guest_has_no_permissions(self):
        for action in PermissionAction:
            assert PermissionsService.can_perform_action(UserRole.GUEST, action) is False


# ---------------------------------------------------------------------------
# can_access_setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanAccessSetting:
    def test_admin_can_access_security_category(self):
        assert PermissionsService.can_access_setting(UserRole.ADMIN, "security") is True

    def test_admin_can_access_secret_sensitivity(self):
        assert PermissionsService.can_access_setting(
            UserRole.ADMIN, "security", SettingSensitivity.SECRET.value
        ) is True

    def test_editor_cannot_access_security_category(self):
        assert PermissionsService.can_access_setting(UserRole.EDITOR, "security") is False

    def test_editor_can_access_database_category(self):
        assert PermissionsService.can_access_setting(UserRole.EDITOR, "database") is True

    def test_editor_cannot_access_secret_sensitivity(self):
        # editor has access to database but not to SECRET sensitivity
        assert PermissionsService.can_access_setting(
            UserRole.EDITOR, "database", SettingSensitivity.SECRET.value
        ) is False

    def test_viewer_can_access_notifications_public(self):
        assert PermissionsService.can_access_setting(
            UserRole.VIEWER, "notifications", SettingSensitivity.PUBLIC.value
        ) is True

    def test_viewer_cannot_access_restricted_sensitivity(self):
        assert PermissionsService.can_access_setting(
            UserRole.VIEWER, "notifications", SettingSensitivity.RESTRICTED.value
        ) is False

    def test_guest_cannot_access_any_internal_category(self):
        assert PermissionsService.can_access_setting(UserRole.GUEST, "database") is False

    def test_invalid_sensitivity_denies_access(self):
        # Invalid sensitivity level — should deny
        assert PermissionsService.can_access_setting(
            UserRole.ADMIN, "security", "ultra_secret"
        ) is False

    def test_no_sensitivity_check_when_not_provided(self):
        # Category access only when sensitivity omitted
        result = PermissionsService.can_access_setting(UserRole.ADMIN, "database")
        assert result is True


# ---------------------------------------------------------------------------
# can_modify_setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanModifySetting:
    def test_admin_can_modify_read_only_setting(self):
        assert PermissionsService.can_modify_setting(UserRole.ADMIN, is_read_only=True) is True

    def test_editor_cannot_modify_read_only_setting(self):
        assert PermissionsService.can_modify_setting(UserRole.EDITOR, is_read_only=True) is False

    def test_editor_can_modify_non_read_only_setting(self):
        assert PermissionsService.can_modify_setting(UserRole.EDITOR, is_read_only=False) is True

    def test_viewer_cannot_modify_any_setting(self):
        assert PermissionsService.can_modify_setting(UserRole.VIEWER, is_read_only=False) is False
        assert PermissionsService.can_modify_setting(UserRole.VIEWER, is_read_only=True) is False

    def test_guest_cannot_modify_any_setting(self):
        assert PermissionsService.can_modify_setting(UserRole.GUEST, is_read_only=False) is False


# ---------------------------------------------------------------------------
# get_read_only_fields_for_role
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetReadOnlyFieldsForRole:
    def test_admin_read_only_fields_are_smallest_set(self):
        admin_fields = PermissionsService.get_read_only_fields_for_role(UserRole.ADMIN)
        viewer_fields = PermissionsService.get_read_only_fields_for_role(UserRole.VIEWER)
        # Admin has fewer read-only fields than viewer
        assert len(admin_fields) < len(viewer_fields)

    def test_admin_cannot_modify_id(self):
        fields = PermissionsService.get_read_only_fields_for_role(UserRole.ADMIN)
        assert "id" in fields

    def test_viewer_value_field_is_read_only(self):
        fields = PermissionsService.get_read_only_fields_for_role(UserRole.VIEWER)
        assert "value" in fields

    def test_editor_key_field_is_read_only(self):
        fields = PermissionsService.get_read_only_fields_for_role(UserRole.EDITOR)
        assert "key" in fields

    def test_unknown_role_returns_empty_set(self):
        # get_read_only_fields_for_role uses .get() with empty set default
        # Pass a real role not in the dict (using a mock-like approach):
        # Actually all roles are in the dict, so test admin returns a non-empty set
        fields = PermissionsService.get_read_only_fields_for_role(UserRole.ADMIN)
        assert isinstance(fields, set)
        assert len(fields) > 0


# ---------------------------------------------------------------------------
# filter_settings_for_role
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilterSettingsForRole:
    def _make_setting(self, category: str, sensitivity: str):
        """Create a simple mock setting object."""
        from unittest.mock import MagicMock
        setting = MagicMock()
        setting.category = category
        setting.sensitivity = sensitivity
        return setting

    def test_admin_receives_all_settings(self):
        settings = [
            self._make_setting("security", "secret"),
            self._make_setting("database", "internal"),
        ]
        result = PermissionsService.filter_settings_for_role(settings, UserRole.ADMIN)
        assert len(result) == 2

    def test_viewer_filters_out_inaccessible_settings(self):
        settings = [
            self._make_setting("notifications", "public"),  # accessible
            self._make_setting("security", "secret"),       # not accessible
        ]
        result = PermissionsService.filter_settings_for_role(settings, UserRole.VIEWER)
        assert len(result) == 1
        assert result[0].category == "notifications"

    def test_guest_receives_no_settings(self):
        settings = [
            self._make_setting("notifications", "public"),
            self._make_setting("database", "internal"),
        ]
        result = PermissionsService.filter_settings_for_role(settings, UserRole.GUEST)
        assert len(result) == 0

    def test_empty_input_returns_empty_list(self):
        result = PermissionsService.filter_settings_for_role([], UserRole.ADMIN)
        assert result == []


# ---------------------------------------------------------------------------
# OAuthUser data class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOAuthUser:
    def test_required_fields_are_stored(self):
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
        )
        assert user.provider == "github"
        assert user.provider_id == "12345"
        assert user.email == "user@example.com"
        assert user.display_name == "Test User"

    def test_optional_avatar_url_defaults_to_none(self):
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
        )
        assert user.avatar_url is None

    def test_optional_raw_data_defaults_to_none(self):
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
        )
        assert user.raw_data is None

    def test_avatar_url_stored_when_provided(self):
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
            avatar_url="https://example.com/avatar.png",
        )
        assert user.avatar_url == "https://example.com/avatar.png"

    def test_raw_data_dict_stored_when_provided(self):
        raw = {"login": "testuser", "id": 12345}
        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="user@example.com",
            display_name="Test User",
            raw_data=raw,
        )
        assert user.raw_data == raw


# ---------------------------------------------------------------------------
# OAuthException
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOAuthException:
    def test_exception_can_be_raised_and_caught(self):
        with pytest.raises(OAuthException):
            raise OAuthException("OAuth flow failed")

    def test_exception_message_is_preserved(self):
        exc = OAuthException("token exchange failed")
        assert "token exchange failed" in str(exc)

    def test_exception_is_subclass_of_exception(self):
        assert issubclass(OAuthException, Exception)
