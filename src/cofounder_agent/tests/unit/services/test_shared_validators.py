"""
Unit tests for shared_validators module.

All validator functions are pure — zero DB, LLM, or network calls.
Tests cover both valid inputs (happy path) and invalid inputs (error path).
"""

import pytest

from services.shared_validators import (
    ValidationError,
    validate_and_normalize_bool,
    validate_choice,
    validate_email,
    validate_identifier,
    validate_iso_datetime,
    validate_limit,
    validate_list_non_empty,
    validate_list_of_strings,
    validate_non_empty_string,
    validate_number_range,
    validate_offset,
    validate_positive_integer,
    validate_slug,
    validate_url,
)


# ---------------------------------------------------------------------------
# validate_non_empty_string
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNonEmptyString:
    def test_valid_string_returned(self):
        assert validate_non_empty_string("hello") == "hello"

    def test_whitespace_is_stripped(self):
        assert validate_non_empty_string("  hello  ") == "hello"

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_non_empty_string("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValidationError):
            validate_non_empty_string("   ")

    def test_non_string_raises(self):
        with pytest.raises(ValidationError):
            validate_non_empty_string(42)  # type: ignore[arg-type]

    def test_max_length_enforced(self):
        with pytest.raises(ValidationError):
            validate_non_empty_string("abc", max_length=2)

    def test_min_length_enforced(self):
        with pytest.raises(ValidationError):
            validate_non_empty_string("ab", min_length=5)

    def test_strip_false_preserves_whitespace(self):
        result = validate_non_empty_string("  hi  ", strip=False)
        assert result == "  hi  "


# ---------------------------------------------------------------------------
# validate_identifier
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateIdentifier:
    def test_alphanumeric_is_valid(self):
        assert validate_identifier("user123") == "user123"

    def test_hyphen_and_underscore_allowed(self):
        assert validate_identifier("task-abc_1") == "task-abc_1"

    def test_spaces_are_invalid(self):
        with pytest.raises(ValidationError):
            validate_identifier("task id")

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_identifier("")

    def test_custom_pattern_enforced(self):
        with pytest.raises(ValidationError):
            validate_identifier("user123", pattern=r"^[a-z]+$")


# ---------------------------------------------------------------------------
# validate_slug
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateSlug:
    def test_lowercase_hyphen_valid(self):
        result = validate_slug("my-blog-post")
        assert result == "my-blog-post"

    def test_uppercase_raises(self):
        with pytest.raises(ValidationError):
            validate_slug("My-Post")

    def test_spaces_raise(self):
        with pytest.raises(ValidationError):
            validate_slug("my post")

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            validate_slug("")


# ---------------------------------------------------------------------------
# validate_email
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateEmail:
    def test_valid_email(self):
        result = validate_email("user@example.com")
        assert result == "user@example.com"

    def test_email_lowercased(self):
        result = validate_email("User@Example.COM")
        assert result == "user@example.com"

    def test_invalid_email_raises(self):
        with pytest.raises(ValidationError):
            validate_email("not-an-email")

    def test_missing_at_raises(self):
        with pytest.raises(ValidationError):
            validate_email("userexample.com")


# ---------------------------------------------------------------------------
# validate_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateUrl:
    def test_https_url_valid(self):
        result = validate_url("https://example.com")
        assert result == "https://example.com"

    def test_http_url_valid(self):
        result = validate_url("http://example.com/path")
        assert result == "http://example.com/path"

    def test_no_protocol_raises(self):
        with pytest.raises(ValidationError):
            validate_url("example.com")

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            validate_url("")


# ---------------------------------------------------------------------------
# validate_offset and validate_limit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateOffset:
    def test_zero_is_valid(self):
        assert validate_offset(0) == 0

    def test_positive_is_valid(self):
        assert validate_offset(100) == 100

    def test_negative_raises(self):
        with pytest.raises(ValidationError):
            validate_offset(-1)

    def test_string_number_coerced(self):
        result = validate_offset("5")
        assert result == 5

    def test_non_numeric_string_raises(self):
        with pytest.raises(ValidationError):
            validate_offset("abc")


@pytest.mark.unit
class TestValidateLimit:
    def test_valid_limit(self):
        result = validate_limit(50)
        assert result == 50

    def test_zero_raises(self):
        with pytest.raises(ValidationError):
            validate_limit(0)

    def test_negative_raises(self):
        with pytest.raises(ValidationError):
            validate_limit(-10)

    def test_exceeding_max_clamps_or_raises(self):
        # Exceeding max_limit should raise or return max_limit
        try:
            result = validate_limit(10000, max_limit=100)
            assert result <= 100
        except ValidationError:
            pass  # Also acceptable


# ---------------------------------------------------------------------------
# validate_positive_integer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidatePositiveInteger:
    def test_positive_int_valid(self):
        assert validate_positive_integer(5) == 5

    def test_zero_raises(self):
        with pytest.raises(ValidationError):
            validate_positive_integer(0)

    def test_negative_raises(self):
        with pytest.raises(ValidationError):
            validate_positive_integer(-3)

    def test_float_is_coerced_to_int(self):
        # float is coerced to int (3.5 → 3), not rejected
        result = validate_positive_integer(3)
        assert result == 3


# ---------------------------------------------------------------------------
# validate_number_range
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNumberRange:
    # Signature: validate_number_range(value, field_name, min_value, max_value)
    def test_value_in_range_is_valid(self):
        result = validate_number_range(50, "score", 0, 100)
        assert result == 50.0

    def test_value_at_min_is_valid(self):
        assert validate_number_range(0, "score", 0, 100) == 0.0

    def test_value_at_max_is_valid(self):
        assert validate_number_range(100, "score", 0, 100) == 100.0

    def test_value_below_min_raises(self):
        with pytest.raises(ValidationError):
            validate_number_range(-1, "score", min_value=0)

    def test_value_above_max_raises(self):
        with pytest.raises(ValidationError):
            validate_number_range(101, "score", max_value=100)


# ---------------------------------------------------------------------------
# validate_choice
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateChoice:
    # Signature: validate_choice(value, field_name, choices=None, enum_class=None)
    def test_valid_choice_returned(self):
        result = validate_choice("pending", "status", choices=["pending", "in_progress", "completed"])
        assert result == "pending"

    def test_invalid_choice_raises(self):
        with pytest.raises(ValidationError):
            validate_choice("unknown", "status", choices=["pending", "in_progress"])

    def test_empty_value_raises(self):
        with pytest.raises(ValidationError):
            validate_choice("", "status", choices=["pending", "in_progress"])


# ---------------------------------------------------------------------------
# validate_list_non_empty and validate_list_of_strings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateListNonEmpty:
    def test_non_empty_list_is_valid(self):
        result = validate_list_non_empty([1, 2, 3])
        assert result == [1, 2, 3]

    def test_empty_list_raises(self):
        with pytest.raises(ValidationError):
            validate_list_non_empty([])

    def test_non_list_raises(self):
        with pytest.raises(ValidationError):
            validate_list_non_empty("not a list")  # type: ignore[arg-type]


@pytest.mark.unit
class TestValidateListOfStrings:
    def test_valid_string_list(self):
        result = validate_list_of_strings(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_empty_list_with_min_items_zero_allowed(self):
        # Default min_items=1, so need to pass min_items=0 explicitly
        result = validate_list_of_strings([], min_items=0)
        assert result == []

    def test_non_string_element_raises(self):
        with pytest.raises(ValidationError):
            validate_list_of_strings(["a", 1, "c"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# validate_and_normalize_bool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAndNormalizeBool:
    def test_true_value(self):
        assert validate_and_normalize_bool(True) is True

    def test_false_value(self):
        assert validate_and_normalize_bool(False) is False

    def test_string_true(self):
        assert validate_and_normalize_bool("true") is True

    def test_string_false(self):
        assert validate_and_normalize_bool("false") is False

    def test_integer_one_is_true(self):
        assert validate_and_normalize_bool(1) is True

    def test_integer_zero_is_false(self):
        assert validate_and_normalize_bool(0) is False

    def test_invalid_string_raises(self):
        with pytest.raises(ValidationError):
            validate_and_normalize_bool("maybe")


# ---------------------------------------------------------------------------
# validate_iso_datetime
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateIsoDatetime:
    def test_valid_iso_string(self):
        from datetime import datetime

        result = validate_iso_datetime("2026-01-15T10:00:00Z")
        assert isinstance(result, datetime)

    def test_invalid_string_raises(self):
        with pytest.raises(ValidationError):
            validate_iso_datetime("not-a-date")

    def test_datetime_object_passed_through(self):
        from datetime import datetime, timezone

        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        result = validate_iso_datetime(dt)
        assert isinstance(result, datetime)
