"""
Permission-Based Settings Service

Handles role-based access control for settings, ensuring users can only
access and modify settings appropriate for their role.

Role Hierarchy:
- Admin (id=1): Full access to all settings, can modify everything
- Editor (id=2): Can read all settings, can modify non-read-only settings
- Viewer (id=3): Can only read non-sensitive settings, no modification
"""

from enum import Enum
from typing import List, Optional

# Note: These imports will be resolved when dependencies are installed
# from models import User, Setting, Role, Permission
# from database import SessionLocal


class UserRole(str, Enum):
    """User roles for permission checking"""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    GUEST = "guest"


class PermissionAction(str, Enum):
    """Permission actions for settings"""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"
    AUDIT = "audit"


class SettingSensitivity(str, Enum):
    """Setting sensitivity levels for access control"""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    SECRET = "secret"


class PermissionsService:
    """Service for managing role-based permissions on settings"""

    # Role-to-permissions mapping
    ROLE_PERMISSIONS = {
        UserRole.ADMIN: {
            PermissionAction.CREATE,
            PermissionAction.READ,
            PermissionAction.UPDATE,
            PermissionAction.DELETE,
            PermissionAction.EXPORT,
            PermissionAction.AUDIT,
        },
        UserRole.EDITOR: {
            PermissionAction.CREATE,
            PermissionAction.READ,
            PermissionAction.UPDATE,  # Cannot update read-only settings
            PermissionAction.AUDIT,
        },
        UserRole.VIEWER: {
            PermissionAction.READ,  # Only non-sensitive
            PermissionAction.AUDIT,  # Can view own audit logs
        },
        UserRole.GUEST: set(),  # No permissions
    }

    # Setting categories accessible by each role
    CATEGORY_ACCESS = {
        UserRole.ADMIN: {
            "database",
            "authentication",
            "api",
            "notifications",
            "system",
            "integration",
            "security",
            "performance",
        },
        UserRole.EDITOR: {
            "database",
            "api",
            "notifications",
            "performance",
        },
        UserRole.VIEWER: {
            "notifications",
        },
        UserRole.GUEST: set(),
    }

    # Sensitivity level access by role
    SENSITIVITY_ACCESS = {
        UserRole.ADMIN: {
            SettingSensitivity.PUBLIC,
            SettingSensitivity.INTERNAL,
            SettingSensitivity.RESTRICTED,
            SettingSensitivity.SECRET,
        },
        UserRole.EDITOR: {
            SettingSensitivity.PUBLIC,
            SettingSensitivity.INTERNAL,
            SettingSensitivity.RESTRICTED,
        },
        UserRole.VIEWER: {
            SettingSensitivity.PUBLIC,
            SettingSensitivity.INTERNAL,
        },
        UserRole.GUEST: {
            SettingSensitivity.PUBLIC,
        },
    }

    @staticmethod
    def get_user_role(user_roles: List[str]) -> UserRole:
        """
        Determine the highest privilege role from a list of role names.

        Args:
            user_roles: List of role names assigned to user

        Returns:
            The highest privilege role
        """
        role_priority = {
            UserRole.ADMIN: 4,
            UserRole.EDITOR: 3,
            UserRole.VIEWER: 2,
            UserRole.GUEST: 1,
        }

        highest_role = UserRole.GUEST
        highest_priority = 0

        for role_name in user_roles:
            try:
                role = UserRole(role_name.lower())
                priority = role_priority.get(role, 0)
                if priority > highest_priority:
                    highest_role = role
                    highest_priority = priority
            except ValueError:
                # Invalid role name, skip
                continue

        return highest_role

    @classmethod
    def can_perform_action(
        cls,
        user_role: UserRole,
        action: PermissionAction,
    ) -> bool:
        """
        Check if user role has permission for specific action.

        Args:
            user_role: User's role
            action: Action to perform

        Returns:
            True if user can perform action, False otherwise
        """
        return action in cls.ROLE_PERMISSIONS.get(user_role, set())

    @classmethod
    def can_access_setting(
        cls,
        user_role: UserRole,
        setting_category: str,
        setting_sensitivity: Optional[str] = None,
    ) -> bool:
        """
        Check if user can access a specific setting.

        Args:
            user_role: User's role
            setting_category: Category of the setting
            setting_sensitivity: Sensitivity level of the setting

        Returns:
            True if user can access setting, False otherwise
        """
        # Check category access
        allowed_categories = cls.CATEGORY_ACCESS.get(user_role, set())
        if setting_category not in allowed_categories:
            return False

        # Check sensitivity access if provided
        if setting_sensitivity:
            try:
                sensitivity = SettingSensitivity(setting_sensitivity.lower())
                allowed_sensitivities = cls.SENSITIVITY_ACCESS.get(user_role, set())
                if sensitivity not in allowed_sensitivities:
                    return False
            except ValueError:
                # Invalid sensitivity level, deny access by default
                return False

        return True

    @classmethod
    def can_modify_setting(
        cls,
        user_role: UserRole,
        is_read_only: bool = False,
    ) -> bool:
        """
        Check if user can modify (update/delete) a specific setting.

        Args:
            user_role: User's role
            is_read_only: Whether the setting is marked as read-only

        Returns:
            True if user can modify setting, False otherwise
        """
        # Check if user has update permission
        if not cls.can_perform_action(user_role, PermissionAction.UPDATE):
            return False

        # Admins can modify read-only settings, others cannot
        if is_read_only and user_role != UserRole.ADMIN:
            return False

        return True

    @classmethod
    def filter_settings_for_role(
        cls,
        settings: List,  # List[Setting] - would need import to type properly
        user_role: UserRole,
    ) -> List:
        """
        Filter settings list based on user role permissions.

        This is used to restrict what settings are returned from queries.

        Args:
            settings: List of settings to filter
            user_role: User's role

        Returns:
            Filtered list of settings user can access
        """
        filtered = []

        for setting in settings:
            # Check if user can access this setting
            if cls.can_access_setting(
                user_role,
                setting.category,
                setting.sensitivity if hasattr(setting, "sensitivity") else None,
            ):
                filtered.append(setting)

        return filtered

    @classmethod
    def get_read_only_fields_for_role(cls, user_role: UserRole) -> set:
        """
        Get the set of setting fields that are read-only for a specific role.

        Args:
            user_role: User's role

        Returns:
            Set of field names that cannot be modified
        """
        read_only_fields = {
            UserRole.GUEST: {
                "id",
                "created_at",
                "updated_at",
                "created_by_id",
                "updated_by_id",
                "category",
                "key",
                "data_type",
            },
            UserRole.VIEWER: {
                "id",
                "created_at",
                "updated_at",
                "created_by_id",
                "updated_by_id",
                "category",
                "key",
                "data_type",
                "is_read_only",
                "value",
            },
            UserRole.EDITOR: {
                "id",
                "created_at",
                "updated_at",
                "created_by_id",
                "key",
                "category",
                "data_type",
            },
            UserRole.ADMIN: {
                "id",
                "created_at",
                "updated_at",
                "created_by_id",
            },
        }

        return read_only_fields.get(user_role, set())

    @classmethod
    def audit_log_accessible(
        cls,
        user_role: UserRole,
        log_user_id: int,
        current_user_id: int,
    ) -> bool:
        """
        Check if user can access specific audit log entry.

        Args:
            user_role: User's role
            log_user_id: ID of user who made the change (in audit log)
            current_user_id: ID of current user

        Returns:
            True if user can access this audit log entry
        """
        # Admins can see all audit logs
        if user_role == UserRole.ADMIN:
            return True

        # Editors can see audit logs for changes to settings they can modify
        if user_role == UserRole.EDITOR:
            return True

        # Viewers can only see audit logs for their own changes
        if user_role == UserRole.VIEWER:
            return log_user_id == current_user_id

        # Guests cannot access audit logs
        return False

    @classmethod
    def get_query_filters_for_role(cls, user_role: UserRole) -> dict:
        """
        Get database query filters based on user role.

        Used to build WHERE clauses for filtering settings in queries.

        Args:
            user_role: User's role

        Returns:
            Dictionary of filter conditions for database query
        """
        filters = {
            UserRole.ADMIN: {
                # Admins see everything
            },
            UserRole.EDITOR: {
                # Editors see all except system-critical
                "is_read_only": False,  # or is_read_only can be overridden
            },
            UserRole.VIEWER: {
                # Viewers see only non-sensitive, read-only
                "sensitivity": {"in": ["public", "internal"]},
                "is_read_only": True,
            },
            UserRole.GUEST: {
                # Guests see only public settings, read-only
                "sensitivity": "public",
                "is_read_only": True,
            },
        }

        return filters.get(user_role, {})

    @classmethod
    def mask_sensitive_value(
        cls,
        value: str,
        is_encrypted: bool = False,
        user_role: UserRole = UserRole.GUEST,
    ) -> str:
        """
        Mask or preview a sensitive value based on user role and encryption status.

        Args:
            value: The actual value
            is_encrypted: Whether the value is encrypted
            user_role: User's role

        Returns:
            Masked or full value depending on role
        """
        # Admins can see full values
        if user_role == UserRole.ADMIN:
            return value

        # Editors can see full values of non-encrypted settings
        if user_role == UserRole.EDITOR and not is_encrypted:
            return value

        # Others get preview only
        if len(value) <= 10:
            return f"{value}..."
        else:
            return f"{value[:10]}..."

    @classmethod
    def validate_permission_action(
        cls,
        user_role: UserRole,
        action: PermissionAction,
        setting_category: Optional[str] = None,
        is_read_only: bool = False,
    ) -> tuple[bool, str]:
        """
        Comprehensive permission check combining all factors.

        Args:
            user_role: User's role
            action: Action to perform
            setting_category: Category of setting (optional)
            is_read_only: Whether setting is read-only

        Returns:
            Tuple of (is_allowed, reason_if_denied)
        """
        # Check basic permission
        if not cls.can_perform_action(user_role, action):
            return False, f"Role '{user_role}' does not have permission for '{action}'"

        # Check category access if provided
        if setting_category:
            allowed_categories = cls.CATEGORY_ACCESS.get(user_role, set())
            if setting_category not in allowed_categories:
                return False, f"Role '{user_role}' cannot access '{setting_category}' settings"

        # Check read-only constraint for modify actions
        if action in {PermissionAction.UPDATE, PermissionAction.DELETE}:
            if is_read_only and user_role != UserRole.ADMIN:
                return False, f"Role '{user_role}' cannot modify read-only settings"

        return True, ""

    @staticmethod
    def get_role_description(role: UserRole) -> str:
        """Get human-readable description of a role."""
        descriptions = {
            UserRole.ADMIN: "Administrator - Full access to all settings",
            UserRole.EDITOR: "Editor - Can read and modify non-read-only settings",
            UserRole.VIEWER: "Viewer - Can only read non-sensitive settings",
            UserRole.GUEST: "Guest - No permissions",
        }
        return descriptions.get(role, "Unknown role")
