"""
Shared validation utilities for API routes.

Provides reusable field validators for common patterns across routes:
- Email validation
- URL validation
- Pagination parameters
- String constraints (non-empty, length limits)
- Date range validation
- Enum validation
- ID format validation

Usage:
    from pydantic import BaseModel, field_validator
    from services.shared_validators import validate_email, validate_non_empty_string

    class UserRequest(BaseModel):
        email: str
        name: str
        
        @field_validator('email')
        @classmethod
        def validate_email_field(cls, v: str) -> str:
            return validate_email(v)
        
        @field_validator('name')
        @classmethod
        def validate_name_field(cls, v: str) -> str:
            return validate_non_empty_string(v, field_name='name', max_length=255)
"""

import re
from datetime import datetime
from typing import Any, List, Optional, Set, Type, TypeVar
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class ValidationError(ValueError):
    """Raised when validation fails."""

    def __init__(self, field: str, message: str, code: str = "VALIDATION_ERROR"):
        self.field = field
        self.message = message
        self.code = code
        super().__init__(f"{field}: {message}")


# ============================================================================
# STRING VALIDATORS
# ============================================================================


def validate_non_empty_string(
    value: Any,
    field_name: str = "value",
    min_length: int = 1,
    max_length: Optional[int] = None,
    strip: bool = True,
) -> str:
    """
    Validate that a value is a non-empty string within length constraints.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        min_length: Minimum string length (default: 1)
        max_length: Maximum string length (optional)
        strip: Whether to strip whitespace (default: True)

    Returns:
        Validated and processed string

    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(value, str):
        raise ValidationError(
            field_name, f"Expected string, got {type(value).__name__}", "TYPE_ERROR"
        )

    if strip:
        value = value.strip()

    if len(value) < min_length:
        if min_length == 1:
            raise ValidationError(
                field_name, f"Cannot be empty", "REQUIRED"
            )
        raise ValidationError(
            field_name,
            f"Must be at least {min_length} characters",
            "MIN_LENGTH",
        )

    if max_length and len(value) > max_length:
        raise ValidationError(
            field_name,
            f"Must be at most {max_length} characters",
            "MAX_LENGTH",
        )

    return value


def validate_identifier(
    value: Any,
    field_name: str = "id",
    pattern: Optional[str] = None,
) -> str:
    """
    Validate an identifier (UUID, slug, etc.).

    Args:
        value: Value to validate
        field_name: Field name for error messages
        pattern: Regex pattern for identifier format (default: alphanumeric + hyphen/underscore)

    Returns:
        Validated identifier

    Raises:
        ValidationError: If validation fails
    """
    if pattern is None:
        pattern = r"^[a-zA-Z0-9_\-]+$"

    value = validate_non_empty_string(value, field_name)

    if not re.match(pattern, value):
        raise ValidationError(
            field_name,
            f"Invalid identifier format",
            "INVALID_FORMAT",
        )

    return value


def validate_slug(value: Any, field_name: str = "slug") -> str:
    """
    Validate a URL-safe slug (lowercase alphanumeric + hyphens).

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Validated slug

    Raises:
        ValidationError: If validation fails
    """
    value = validate_non_empty_string(value, field_name, max_length=255)

    # Slug pattern: lowercase letters, numbers, hyphens, no leading/trailing hyphens
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", value):
        raise ValidationError(
            field_name,
            "Must contain only lowercase letters, numbers, and hyphens",
            "INVALID_SLUG",
        )

    return value


# ============================================================================
# EMAIL & URL VALIDATORS
# ============================================================================


def validate_email(value: Any, field_name: str = "email") -> str:
    """
    Validate email address format.

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Validated email

    Raises:
        ValidationError: If validation fails
    """
    value = validate_non_empty_string(value, field_name, max_length=255)

    # RFC 5322 simplified pattern
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(email_pattern, value):
        raise ValidationError(
            field_name,
            "Invalid email address format",
            "INVALID_EMAIL",
        )

    return value.lower()


def validate_url(
    value: Any,
    field_name: str = "url",
    allowed_schemes: Optional[Set[str]] = None,
    require_tld: bool = True,
) -> str:
    """
    Validate URL format.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        allowed_schemes: Allowed URL schemes (default: http, https)
        require_tld: Require top-level domain (default: True)

    Returns:
        Validated URL

    Raises:
        ValidationError: If validation fails
    """
    if allowed_schemes is None:
        allowed_schemes = {"http", "https"}

    value = validate_non_empty_string(value, field_name, max_length=2048)

    try:
        parsed = urlparse(value)
    except Exception as e:
        raise ValidationError(
            field_name,
            "Invalid URL format",
            "INVALID_URL",
        ) from e

    if not parsed.scheme or parsed.scheme not in allowed_schemes:
        raise ValidationError(
            field_name,
            f"URL scheme must be one of: {', '.join(allowed_schemes)}",
            "INVALID_SCHEME",
        )

    if not parsed.netloc:
        raise ValidationError(
            field_name,
            "URL must include a domain",
            "INVALID_URL",
        )

    if require_tld and "." not in parsed.netloc:
        raise ValidationError(
            field_name,
            "Domain must include a TLD (e.g., .com)",
            "INVALID_TLD",
        )

    return value


# ============================================================================
# PAGINATION VALIDATORS
# ============================================================================


def validate_offset(value: Any, field_name: str = "offset") -> int:
    """
    Validate pagination offset.

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Validated offset (>= 0)

    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                field_name,
                "Must be an integer",
                "TYPE_ERROR",
            ) from e

    if value < 0:
        raise ValidationError(
            field_name,
            "Must be 0 or greater",
            "INVALID_RANGE",
        )

    return value


def validate_limit(
    value: Any,
    field_name: str = "limit",
    min_limit: int = 1,
    max_limit: int = 1000,
    default_limit: int = 10,
) -> int:
    """
    Validate pagination limit.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        min_limit: Minimum limit (default: 1)
        max_limit: Maximum limit (default: 1000)
        default_limit: Default if None (default: 10)

    Returns:
        Validated limit

    Raises:
        ValidationError: If validation fails
    """
    if value is None:
        return default_limit

    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                field_name,
                "Must be an integer",
                "TYPE_ERROR",
            ) from e

    if value < min_limit or value > max_limit:
        raise ValidationError(
            field_name,
            f"Must be between {min_limit} and {max_limit}",
            "INVALID_RANGE",
        )

    return value


# ============================================================================
# NUMERIC VALIDATORS
# ============================================================================


def validate_positive_integer(
    value: Any,
    field_name: str = "value",
    allow_zero: bool = False,
) -> int:
    """
    Validate positive integer.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        allow_zero: Whether to allow 0 (default: False)

    Returns:
        Validated integer

    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(value, int):
        try:
            value = int(value)
        except (ValueError, TypeError) as e:
            raise ValidationError(
                field_name,
                "Must be an integer",
                "TYPE_ERROR",
            ) from e

    min_value = 0 if allow_zero else 1
    if value < min_value:
        raise ValidationError(
            field_name,
            f"Must be {min_value} or greater",
            "INVALID_RANGE",
        )

    return value


def validate_number_range(
    value: Any,
    field_name: str = "value",
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
) -> float:
    """
    Validate number is within range.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        min_value: Minimum value (optional)
        max_value: Maximum value (optional)

    Returns:
        Validated number as float

    Raises:
        ValidationError: If validation fails
    """
    try:
        numeric_value = float(value)
    except (ValueError, TypeError) as e:
        raise ValidationError(
            field_name,
            "Must be a number",
            "TYPE_ERROR",
        ) from e

    if min_value is not None and numeric_value < min_value:
        raise ValidationError(
            field_name,
            f"Must be {min_value} or greater",
            "INVALID_RANGE",
        )

    if max_value is not None and numeric_value > max_value:
        raise ValidationError(
            field_name,
            f"Must be {max_value} or less",
            "INVALID_RANGE",
        )

    return numeric_value


# ============================================================================
# DATE/TIME VALIDATORS
# ============================================================================


def validate_iso_datetime(value: Any, field_name: str = "datetime") -> datetime:
    """
    Validate ISO 8601 datetime string.

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Validated datetime object

    Raises:
        ValidationError: If validation fails
    """
    if isinstance(value, datetime):
        return value

    if not isinstance(value, str):
        raise ValidationError(
            field_name,
            "Must be an ISO 8601 datetime string",
            "TYPE_ERROR",
        )

    try:
        # Accept both with and without timezone
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as e:
        raise ValidationError(
            field_name,
            "Must be valid ISO 8601 format (e.g., 2024-01-15T10:30:00Z)",
            "INVALID_DATETIME",
        ) from e


def validate_date_range(
    start_date: Any,
    end_date: Any,
    field_name: str = "date_range",
) -> tuple[datetime, datetime]:
    """
    Validate that end_date >= start_date.

    Args:
        start_date: Start date to validate
        end_date: End date to validate
        field_name: Field name for error messages

    Returns:
        Tuple of (start_date, end_date) as datetime objects

    Raises:
        ValidationError: If validation fails
    """
    start = validate_iso_datetime(start_date, f"{field_name}_start")
    end = validate_iso_datetime(end_date, f"{field_name}_end")

    if end < start:
        raise ValidationError(
            field_name,
            "End date must be after start date",
            "INVALID_RANGE",
        )

    return start, end


# ============================================================================
# ENUM & CHOICE VALIDATORS
# ============================================================================


def validate_choice(
    value: Any,
    field_name: str = "value",
    choices: Optional[List[str]] = None,
    enum_class: Optional[Type] = None,
) -> str:
    """
    Validate value is one of allowed choices.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        choices: List of allowed choices
        enum_class: Python Enum class (alternative to choices)

    Returns:
        Validated choice value

    Raises:
        ValidationError: If validation fails
    """
    value = validate_non_empty_string(value, field_name)

    if enum_class:
        try:
            enum_class[value.upper()]
            return value
        except KeyError as e:
            choices = [e.name.lower() for e in enum_class]

    if choices and value not in choices:
        choices_str = ", ".join(choices)
        raise ValidationError(
            field_name,
            f"Must be one of: {choices_str}",
            "INVALID_CHOICE",
        )

    return value


# ============================================================================
# COLLECTION VALIDATORS
# ============================================================================


def validate_list_non_empty(
    value: Any,
    field_name: str = "items",
    min_items: int = 1,
    max_items: Optional[int] = None,
) -> list:
    """
    Validate list is non-empty within constraints.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        min_items: Minimum items required (default: 1)
        max_items: Maximum items allowed (optional)

    Returns:
        Validated list

    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(value, list):
        raise ValidationError(
            field_name,
            "Must be a list",
            "TYPE_ERROR",
        )

    if len(value) < min_items:
        if min_items == 1:
            raise ValidationError(
                field_name,
                "Cannot be empty",
                "REQUIRED",
            )
        raise ValidationError(
            field_name,
            f"Must have at least {min_items} items",
            "MIN_LENGTH",
        )

    if max_items and len(value) > max_items:
        raise ValidationError(
            field_name,
            f"Must have at most {max_items} items",
            "MAX_LENGTH",
        )

    return value


def validate_list_of_strings(
    value: Any,
    field_name: str = "items",
    min_items: int = 1,
    max_items: Optional[int] = None,
) -> List[str]:
    """
    Validate list contains only non-empty strings.

    Args:
        value: Value to validate
        field_name: Field name for error messages
        min_items: Minimum items required (default: 1)
        max_items: Maximum items allowed (optional)

    Returns:
        Validated list of strings

    Raises:
        ValidationError: If validation fails
    """
    items = validate_list_non_empty(value, field_name, min_items, max_items)

    for idx, item in enumerate(items):
        if not isinstance(item, str):
            raise ValidationError(
                f"{field_name}[{idx}]",
                f"Expected string, got {type(item).__name__}",
                "TYPE_ERROR",
            )
        if not item.strip():
            raise ValidationError(
                f"{field_name}[{idx}]",
                "Cannot be empty",
                "REQUIRED",
            )

    return items


# ============================================================================
# CONVERSION & UTILITY VALIDATORS
# ============================================================================


def validate_and_normalize_bool(value: Any, field_name: str = "value") -> bool:
    """
    Validate and normalize boolean value.

    Accepts: bool, "true"/"false", "yes"/"no", "1"/"0", 1/0

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Boolean value

    Raises:
        ValidationError: If validation fails
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        if value.lower() in ("true", "yes", "1", "on"):
            return True
        if value.lower() in ("false", "no", "0", "off"):
            return False

    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)

    raise ValidationError(
        field_name,
        "Must be a boolean value (true/false, yes/no, 1/0)",
        "TYPE_ERROR",
    )


def validate_json_compatible(value: Any, field_name: str = "value") -> Any:
    """
    Validate value is JSON serializable.

    Args:
        value: Value to validate
        field_name: Field name for error messages

    Returns:
        Original value if valid

    Raises:
        ValidationError: If value is not JSON serializable
    """
    import json

    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError) as e:
        raise ValidationError(
            field_name,
            "Value is not JSON serializable",
            "INVALID_JSON",
        ) from e
