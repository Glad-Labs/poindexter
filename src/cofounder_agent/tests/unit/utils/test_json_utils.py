"""
Unit tests for utils/json_utils.py — parse_json_field().
"""

import pytest

from utils.json_utils import parse_json_field


@pytest.mark.unit
class TestParseJsonFieldNoneAndEmpty:
    def test_none_returns_default_none(self):
        assert parse_json_field(None) is None

    def test_none_returns_custom_default(self):
        assert parse_json_field(None, default={}) == {}

    def test_empty_string_returns_default(self):
        assert parse_json_field("", default={}) == {}

    def test_whitespace_string_returns_default(self):
        assert parse_json_field("   ", default={}) == {}


@pytest.mark.unit
class TestParseJsonFieldAlreadyParsed:
    def test_dict_returned_as_is(self):
        data = {"key": "value"}
        assert parse_json_field(data, default={}) is data

    def test_list_returned_as_is(self):
        data = [1, 2, 3]
        assert parse_json_field(data, default=[]) is data

    def test_empty_dict_returned_as_is(self):
        data = {}
        assert parse_json_field(data, default={"fallback": True}) is data


@pytest.mark.unit
class TestParseJsonFieldJsonString:
    def test_json_object_string_parsed(self):
        result = parse_json_field('{"a": 1}', default={})
        assert result == {"a": 1}

    def test_json_array_string_parsed(self):
        result = parse_json_field('["x", "y"]', default=[])
        assert result == ["x", "y"]

    def test_json_string_with_whitespace_parsed(self):
        result = parse_json_field('  {"b": 2}  ', default={})
        assert result == {"b": 2}

    def test_json_null_parses_to_none(self):
        # json.loads("null") == None — the caller gets back Python None
        result = parse_json_field("null", default={})
        assert result is None


@pytest.mark.unit
class TestParseJsonFieldInvalidJson:
    def test_invalid_json_returns_default(self):
        result = parse_json_field("not json at all", default={})
        assert result == {}

    def test_invalid_json_list_default(self):
        result = parse_json_field("broken[", default=[])
        assert result == []

    def test_invalid_json_emits_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            parse_json_field("invalid", default={}, field_name="seo_keywords", record_id="task-99")
        assert "seo_keywords" in caplog.text
        assert "task-99" in caplog.text


@pytest.mark.unit
class TestParseJsonFieldUnexpectedType:
    def test_bytes_returns_default(self):
        result = parse_json_field(b'{"x": 1}', default={})
        assert result == {}

    def test_integer_returns_default(self):
        result = parse_json_field(42, default={})
        assert result == {}
