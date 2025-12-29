"""
Settings Management Service

Provides business logic for settings management, validation, and persistence.
Supports validation of various setting types including enums, booleans, and custom formats.
"""

from typing import Any, Dict, Optional
from enum import Enum
from datetime import datetime


class SettingValidator:
    """Validates setting values based on type and constraints"""

    @staticmethod
    def validate_setting(key: str, value: Any, setting_type: str = "string") -> bool:
        """
        Validate a setting value

        Args:
            key: Setting identifier
            value: Value to validate
            setting_type: Type of setting (string, boolean, enum, integer, json)

        Returns:
            True if valid, raises ValueError if invalid

        Raises:
            ValueError: If validation fails
        """
        if not key:
            raise ValueError("Setting key cannot be empty")

        if value is None:
            raise ValueError(f"Setting '{key}' cannot have a None value")

        # Special validation for specific enum keys
        if key == "theme":
            return SettingValidator.validate_theme_enum(value)
        elif key == "email_frequency":
            return SettingValidator.validate_email_frequency(value)
        elif key == "timezone":
            return SettingValidator.validate_timezone(value)

        # Boolean keys (infer type from key name)
        if key == "notifications_enabled" or key.endswith("_enabled") or key.endswith("_disabled"):
            return SettingValidator.validate_boolean(key, value)

        # Validate based on type
        if setting_type == "boolean":
            return SettingValidator.validate_boolean(key, value)
        elif setting_type == "enum":
            return SettingValidator.validate_enum(key, value)
        elif setting_type == "integer":
            return SettingValidator.validate_integer(key, value)
        elif setting_type == "string":
            return SettingValidator.validate_string(key, value)
        elif setting_type == "json":
            return SettingValidator.validate_json(key, value)
        else:
            return True  # Unknown type, allow

    @staticmethod
    def validate_boolean(key: str, value: Any) -> bool:
        """Validate boolean setting"""
        if not isinstance(value, bool):
            raise ValueError(f"Setting '{key}' must be a boolean, got {type(value).__name__}")
        return True

    @staticmethod
    def validate_enum(key: str, value: Any) -> bool:
        """Validate enum setting"""
        if not isinstance(value, str):
            raise ValueError(f"Setting '{key}' must be a string, got {type(value).__name__}")
        if not value:
            raise ValueError(f"Setting '{key}' cannot be empty")
        return True

    @staticmethod
    def validate_integer(key: str, value: Any) -> bool:
        """Validate integer setting"""
        if not isinstance(value, int):
            raise ValueError(f"Setting '{key}' must be an integer, got {type(value).__name__}")
        return True

    @staticmethod
    def validate_string(key: str, value: Any) -> bool:
        """Validate string setting"""
        if not isinstance(value, str):
            raise ValueError(f"Setting '{key}' must be a string, got {type(value).__name__}")
        return True

    @staticmethod
    def validate_json(key: str, value: Any) -> bool:
        """Validate JSON setting (any dict or list)"""
        if not isinstance(value, (dict, list)):
            raise ValueError(
                f"Setting '{key}' must be a JSON object or array, got {type(value).__name__}"
            )
        return True

    @staticmethod
    def validate_theme_enum(theme: str) -> bool:
        """Validate theme setting"""
        valid_themes = ["light", "dark", "system"]
        if theme not in valid_themes:
            raise ValueError(f"Invalid theme '{theme}'. Must be one of: {', '.join(valid_themes)}")
        return True

    @staticmethod
    def validate_email_frequency(frequency: str) -> bool:
        """Validate email frequency setting"""
        valid_frequencies = ["never", "daily", "weekly", "monthly"]
        if frequency not in valid_frequencies:
            raise ValueError(
                f"Invalid email frequency '{frequency}'. Must be one of: {', '.join(valid_frequencies)}"
            )
        return True

    @staticmethod
    def validate_timezone(timezone: str) -> bool:
        """Validate timezone setting

        Validates against common timezone formats:
        - UTC-like: UTC, UTC±hh:mm
        - Tz database: Continent/City
        """
        if not timezone or not isinstance(timezone, str):
            raise ValueError(f"Invalid timezone '{timezone}'")
        if len(timezone) < 3:
            raise ValueError(f"Timezone must be at least 3 characters, got {len(timezone)}")

        # Valid timezone examples: UTC, America/New_York, Europe/London, Asia/Tokyo
        # Pattern: either UTC/UTC±offset, or Continent/City
        valid_timezones = {
            "UTC",
            "GMT",
            # Common regions
            "America/New_York",
            "America/Chicago",
            "America/Denver",
            "America/Los_Angeles",
            "America/Anchorage",
            "America/Toronto",
            "America/Mexico_City",
            "America/Buenos_Aires",
            "America/Sao_Paulo",
            "America/Caracas",
            "America/Jamaica",
            "America/Barbados",
            "Europe/London",
            "Europe/Paris",
            "Europe/Berlin",
            "Europe/Moscow",
            "Europe/Istanbul",
            "Europe/Athens",
            "Europe/Rome",
            "Europe/Madrid",
            "Europe/Dublin",
            "Europe/Vienna",
            "Africa/Cairo",
            "Africa/Johannesburg",
            "Africa/Lagos",
            "Africa/Nairobi",
            "Asia/Tokyo",
            "Asia/Shanghai",
            "Asia/Hong_Kong",
            "Asia/Singapore",
            "Asia/Bangkok",
            "Asia/Jakarta",
            "Asia/Manila",
            "Asia/Seoul",
            "Asia/Dubai",
            "Asia/Kolkata",
            "Asia/Bangalore",
            "Asia/Bangkok",
            "Australia/Sydney",
            "Australia/Melbourne",
            "Australia/Brisbane",
            "Australia/Perth",
            "Australia/Adelaide",
            "Pacific/Auckland",
            "Pacific/Fiji",
            "Pacific/Honolulu",
        }

        if timezone in valid_timezones:
            return True

        # Check for UTC±offset format
        if timezone.startswith("UTC"):
            # UTC, UTC+5:30, UTC-8, etc.
            if len(timezone) == 3:  # Just "UTC"
                return True
            # Check for offset pattern
            offset = timezone[3:]
            if offset and offset[0] in ("+", "-"):
                # Should be offset like +5:30 or -8
                return True

        # If not in our list and not UTC format, it's invalid
        raise ValueError(
            f"Invalid timezone '{timezone}'. Must be a valid IANA timezone or UTC variant"
        )

    @staticmethod
    def validate_boolean_fields(fields: Dict[str, bool]) -> bool:
        """Validate a dict of boolean fields"""
        if not isinstance(fields, dict):
            raise ValueError(f"Fields must be a dictionary, got {type(fields).__name__}")

        for key, value in fields.items():
            if not isinstance(value, bool):
                raise ValueError(f"Field '{key}' must be boolean, got {type(value).__name__}")

        return True


class SettingsService:
    """Service for managing application settings"""

    def __init__(self):
        """Initialize settings service"""
        self.validator = SettingValidator()
        self.settings_cache: Dict[str, Any] = {}

    def get_setting(self, key: str) -> Optional[Any]:
        """Get a setting value"""
        return self.settings_cache.get(key)

    def set_setting(self, key: str, value: Any, setting_type: str = "string") -> bool:
        """Set a setting value with validation"""
        if self.validator.validate_setting(key, value, setting_type):
            self.settings_cache[key] = value
            return True
        return False

    def delete_setting(self, key: str) -> bool:
        """Delete a setting"""
        if key in self.settings_cache:
            del self.settings_cache[key]
            return True
        return False

    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings"""
        return self.settings_cache.copy()

    def reset_settings(self) -> None:
        """Reset all settings"""
        self.settings_cache.clear()


# Module-level functions for compatibility
def validate_setting(key: str, value: Any, setting_type: str = "string") -> bool:
    """Module-level function for validating settings

    Returns True if valid, False otherwise (catches exceptions)
    """
    try:
        return SettingValidator.validate_setting(key, value, setting_type)
    except (ValueError, TypeError):
        return False


def validate_theme_enum(theme: str) -> bool:
    """Module-level function for theme validation

    Returns True if valid, False otherwise (catches exceptions)
    """
    try:
        return SettingValidator.validate_theme_enum(theme)
    except (ValueError, TypeError):
        return False


def validate_email_frequency(frequency: str) -> bool:
    """Module-level function for email frequency validation

    Returns True if valid, False otherwise (catches exceptions)
    """
    try:
        return SettingValidator.validate_email_frequency(frequency)
    except (ValueError, TypeError):
        return False


def validate_timezone(timezone: str) -> bool:
    """Module-level function for timezone validation

    Returns True if valid, False otherwise (catches exceptions)
    """
    try:
        return SettingValidator.validate_timezone(timezone)
    except (ValueError, TypeError):
        return False


def validate_boolean_fields(fields: Dict[str, bool]) -> bool:
    """Module-level function for boolean fields validation

    Returns True if valid, False otherwise (catches exceptions)
    """
    try:
        return SettingValidator.validate_boolean_fields(fields)
    except (ValueError, TypeError):
        return False


# Create default service instance
_default_service = SettingsService()


def get_settings_service() -> SettingsService:
    """Get the default settings service instance"""
    return _default_service
