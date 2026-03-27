"""
Unit tests for utils.json_encoder module.

All tests are pure — zero DB, LLM, or network calls.
Covers DecimalEncoder, safe_json_dumps, safe_json_load, convert_decimals.
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from utils.json_encoder import DecimalEncoder, convert_decimals, safe_json_dumps, safe_json_load

# ---------------------------------------------------------------------------
# DecimalEncoder
# ---------------------------------------------------------------------------


class TestDecimalEncoder:
    """Tests for the custom JSON encoder class."""

    def test_encodes_decimal_as_float(self):
        data = {"price": Decimal("19.99")}
        result = json.dumps(data, cls=DecimalEncoder)
        assert result == '{"price": 19.99}'

    def test_encodes_uuid_as_string(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        data = {"id": uid}
        result = json.dumps(data, cls=DecimalEncoder)
        assert '"12345678-1234-5678-1234-567812345678"' in result

    def test_encodes_datetime_as_iso_string(self):
        dt = datetime(2026, 3, 12, 10, 30, 0, tzinfo=timezone.utc)
        data = {"created_at": dt}
        result = json.dumps(data, cls=DecimalEncoder)
        assert "2026-03-12" in result

    def test_encodes_date_as_iso_string(self):
        d = date(2026, 3, 12)
        data = {"date": d}
        result = json.dumps(data, cls=DecimalEncoder)
        assert "2026-03-12" in result

    def test_falls_through_to_default_for_unknown_type(self):
        class _Custom:
            pass

        data = {"obj": _Custom()}
        with pytest.raises(TypeError):
            json.dumps(data, cls=DecimalEncoder)

    def test_encodes_zero_decimal(self):
        result = json.dumps({"v": Decimal("0")}, cls=DecimalEncoder)
        assert '"v": 0.0' in result

    def test_encodes_negative_decimal(self):
        result = json.dumps({"v": Decimal("-3.14")}, cls=DecimalEncoder)
        assert "-3.14" in result

    def test_encodes_mixed_nested_structure(self):
        uid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        dt = date(2026, 1, 1)
        data = {
            "price": Decimal("9.99"),
            "id": uid,
            "date": dt,
            "name": "test",
        }
        result = json.dumps(data, cls=DecimalEncoder)
        parsed = json.loads(result)
        assert parsed["price"] == 9.99
        assert parsed["id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert parsed["date"] == "2026-01-01"
        assert parsed["name"] == "test"


# ---------------------------------------------------------------------------
# safe_json_dumps
# ---------------------------------------------------------------------------


class TestSafeJsonDumps:
    """Tests for the safe_json_dumps convenience wrapper."""

    def test_serializes_plain_dict(self):
        result = safe_json_dumps({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_serializes_decimal(self):
        result = safe_json_dumps({"price": Decimal("19.99")})
        parsed = json.loads(result)
        assert parsed["price"] == pytest.approx(19.99)

    def test_serializes_uuid(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        result = safe_json_dumps({"id": uid})
        parsed = json.loads(result)
        assert parsed["id"] == "12345678-1234-5678-1234-567812345678"

    def test_serializes_datetime(self):
        dt = datetime(2026, 3, 12, 0, 0, 0)
        result = safe_json_dumps({"ts": dt})
        parsed = json.loads(result)
        assert "2026-03-12" in parsed["ts"]

    def test_passes_kwargs_through(self):
        data = {"b": 2, "a": 1}
        result = safe_json_dumps(data, sort_keys=True)
        assert result.index('"a"') < result.index('"b"')

    def test_serializes_empty_dict(self):
        assert safe_json_dumps({}) == "{}"

    def test_serializes_list(self):
        result = safe_json_dumps([Decimal("1.5"), Decimal("2.5")])
        parsed = json.loads(result)
        assert parsed == [1.5, 2.5]


# ---------------------------------------------------------------------------
# safe_json_load
# ---------------------------------------------------------------------------


class TestSafeJsonLoad:
    """Tests for the safe_json_load helper."""

    def test_parses_json_string(self):
        assert safe_json_load('{"k": 1}') == {"k": 1}

    def test_returns_already_parsed_dict_unchanged(self):
        d = {"k": 1}
        assert safe_json_load(d) is d

    def test_returns_already_parsed_list_unchanged(self):
        lst = [1, 2, 3]
        assert safe_json_load(lst) is lst

    def test_returns_fallback_on_bad_json(self):
        assert safe_json_load("not valid json", fallback=[]) == []

    def test_default_fallback_is_none(self):
        assert safe_json_load("bad json") is None

    def test_parses_json_array_string(self):
        result = safe_json_load('["a", "b"]')
        assert result == ["a", "b"]

    def test_returns_integer_unchanged(self):
        assert safe_json_load(42) == 42

    def test_returns_none_unchanged(self):
        assert safe_json_load(None) is None

    def test_empty_string_returns_fallback(self):
        assert safe_json_load("", fallback="default") == "default"

    def test_parses_nested_json(self):
        result = safe_json_load('{"nested": {"key": true}}')
        assert result == {"nested": {"key": True}}


# ---------------------------------------------------------------------------
# convert_decimals
# ---------------------------------------------------------------------------


class TestConvertDecimals:
    """Tests for the recursive convert_decimals utility."""

    def test_converts_top_level_decimal(self):
        assert convert_decimals(Decimal("3.14")) == pytest.approx(3.14)

    def test_converts_decimal_in_dict(self):
        result = convert_decimals({"price": Decimal("19.99")})
        assert result == {"price": pytest.approx(19.99)}

    def test_converts_decimal_in_list(self):
        result = convert_decimals([Decimal("1.0"), Decimal("2.5")])
        assert result == [pytest.approx(1.0), pytest.approx(2.5)]

    def test_converts_decimal_in_tuple(self):
        result = convert_decimals((Decimal("1.0"), "text"))
        assert isinstance(result, tuple)
        assert result[0] == pytest.approx(1.0)
        assert result[1] == "text"

    def test_converts_uuid_to_string(self):
        uid = UUID("12345678-1234-5678-1234-567812345678")
        result = convert_decimals(uid)
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_converts_datetime_to_iso(self):
        dt = datetime(2026, 3, 12, 0, 0, 0)
        result = convert_decimals(dt)
        assert "2026-03-12" in result

    def test_converts_date_to_iso(self):
        d = date(2026, 3, 12)
        result = convert_decimals(d)
        assert result == "2026-03-12"

    def test_passes_through_string(self):
        assert convert_decimals("hello") == "hello"

    def test_passes_through_integer(self):
        assert convert_decimals(42) == 42

    def test_passes_through_none(self):
        assert convert_decimals(None) is None

    def test_converts_deeply_nested_structure(self):
        data = {
            "level1": {
                "level2": {
                    "price": Decimal("9.99"),
                    "items": [Decimal("1.5"), "text"],
                }
            }
        }
        result = convert_decimals(data)
        assert result["level1"]["level2"]["price"] == pytest.approx(9.99)
        assert result["level1"]["level2"]["items"][0] == pytest.approx(1.5)
        assert result["level1"]["level2"]["items"][1] == "text"

    def test_converts_list_of_dicts(self):
        data = [{"v": Decimal("1.1")}, {"v": Decimal("2.2")}]
        result = convert_decimals(data)
        assert result[0]["v"] == pytest.approx(1.1)
        assert result[1]["v"] == pytest.approx(2.2)

    def test_handles_empty_dict(self):
        assert convert_decimals({}) == {}

    def test_handles_empty_list(self):
        assert convert_decimals([]) == []

    def test_handles_empty_tuple(self):
        assert convert_decimals(()) == ()
