"""
Unit tests for services.database_mixin.DatabaseServiceMixin

Tests cover:
- UUID → str conversion
- Decimal → float conversion
- JSONB string fields → parsed dict/list
- Timestamp fields → ISO string
- Rows with missing optional fields (no KeyError)
- Plain dict input passthrough
- Non-dict / no-keys input handled gracefully
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from services.database_mixin import DatabaseServiceMixin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRecord:
    """Minimal asyncpg Record-like: has .keys() and supports dict()."""

    def __init__(self, data: dict):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data.items())

    def items(self):
        return self._data.items()

    def __getitem__(self, key):
        return self._data[key]


def convert(data: dict) -> dict:
    """Convenience wrapper."""
    return DatabaseServiceMixin._convert_row_to_dict(data)


# ---------------------------------------------------------------------------
# UUID conversion
# ---------------------------------------------------------------------------


class TestUUIDConversion:
    def test_uuid_id_converted_to_str(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        result = convert({"id": uid, "name": "Alice"})
        assert result["id"] == "12345678-1234-5678-1234-567812345678"
        assert isinstance(result["id"], str)

    def test_str_id_left_unchanged(self):
        result = convert({"id": "already-a-string"})
        assert result["id"] == "already-a-string"

    def test_none_id_not_converted(self):
        result = convert({"id": None})
        assert result["id"] is None

    def test_no_id_field_no_error(self):
        result = convert({"name": "Bob"})
        assert result["name"] == "Bob"


# ---------------------------------------------------------------------------
# Decimal conversion
# ---------------------------------------------------------------------------


class TestDecimalConversion:
    def test_decimal_converted_to_float(self):
        result = convert({"cost": Decimal("3.14")})
        assert isinstance(result["cost"], float)
        assert abs(result["cost"] - 3.14) < 1e-9

    def test_multiple_decimal_fields(self):
        result = convert({"a": Decimal("1.5"), "b": Decimal("2.5")})
        assert result["a"] == 1.5
        assert result["b"] == 2.5

    def test_non_decimal_numeric_left_alone(self):
        result = convert({"count": 42, "ratio": 0.5})
        assert result["count"] == 42
        assert result["ratio"] == 0.5


# ---------------------------------------------------------------------------
# JSONB string fields
# ---------------------------------------------------------------------------


class TestJSONBFields:
    def test_tags_string_parsed_to_list(self):
        result = convert({"tags": '["python", "fastapi"]'})
        assert result["tags"] == ["python", "fastapi"]

    def test_task_metadata_string_parsed_to_dict(self):
        payload = json.dumps({"retry": 1})
        result = convert({"task_metadata": payload})
        assert result["task_metadata"] == {"retry": 1}

    def test_result_string_parsed_to_dict(self):
        result = convert({"result": '{"status": "ok"}'})
        assert result["result"] == {"status": "ok"}

    def test_invalid_json_tags_fallback_to_list(self):
        result = convert({"tags": "not-json"})
        assert result["tags"] == []

    def test_invalid_json_task_metadata_fallback_to_dict(self):
        result = convert({"task_metadata": "invalid"})
        assert result["task_metadata"] == {}

    def test_already_dict_not_re_parsed(self):
        d = {"key": "value"}
        result = convert({"metadata": d})
        assert result["metadata"] == d

    def test_none_jsonb_field_left_as_none(self):
        result = convert({"result": None})
        assert result["result"] is None


# ---------------------------------------------------------------------------
# Timestamp conversion
# ---------------------------------------------------------------------------


class TestTimestampConversion:
    def test_datetime_converted_to_iso_string(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = convert({"created_at": dt})
        assert isinstance(result["created_at"], str)
        assert "2025-06-01" in result["created_at"]

    def test_all_timestamp_fields_converted(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        fields = ["created_at", "updated_at", "started_at", "completed_at",
                  "last_used", "evaluation_timestamp", "refinement_timestamp", "modified_at"]
        data = {f: dt for f in fields}
        result = convert(data)
        for f in fields:
            assert isinstance(result[f], str), f"{f} not converted"

    def test_none_timestamp_left_as_none(self):
        result = convert({"created_at": None})
        assert result["created_at"] is None

    def test_string_timestamp_left_alone(self):
        result = convert({"created_at": "2025-01-01T00:00:00"})
        assert result["created_at"] == "2025-01-01T00:00:00"


# ---------------------------------------------------------------------------
# FakeRecord (asyncpg-like) input
# ---------------------------------------------------------------------------


class TestFakeRecordInput:
    def test_fake_record_with_keys_converted(self):
        uid = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        record = FakeRecord({"id": uid, "cost": Decimal("9.99")})
        result = DatabaseServiceMixin._convert_row_to_dict(record)
        assert result["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        assert result["cost"] == 9.99


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_dict(self):
        result = convert({})
        assert result == {}

    def test_non_dict_non_keys_input_returns_empty(self):
        # Input has no .keys() and is not a dict — returns empty
        result = DatabaseServiceMixin._convert_row_to_dict("just a string")
        assert result == {}

    def test_extra_fields_passed_through(self):
        result = convert({"custom_field": "hello", "num": 123})
        assert result["custom_field"] == "hello"
        assert result["num"] == 123
