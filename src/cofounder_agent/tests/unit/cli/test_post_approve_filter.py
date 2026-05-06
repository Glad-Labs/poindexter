"""Unit tests for the bulk-approval filter parser.

Glad-Labs/poindexter#338. The parser is its own module so the SQL
construction is unit-testable without standing up a Click runner.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from poindexter.cli._post_approve_filter import (
    FilterParseError,
    parse_filter,
)


class TestSingleClause:
    def test_state_clause_emits_pending_gate_exists(self):
        parsed = parse_filter("state=draft")
        assert (
            "EXISTS (SELECT 1 FROM post_approval_gates g" in parsed.where_sql
        )
        assert "g.state = 'pending'" in parsed.where_sql
        assert "g.gate_name = $1" in parsed.where_sql
        assert parsed.params == ["draft"]
        assert parsed.clauses == [("state", "draft")]

    def test_gate_kind_is_alias_of_state(self):
        parsed = parse_filter("gate_kind=topic")
        assert "g.gate_name = $1" in parsed.where_sql
        assert parsed.params == ["topic"]

    def test_created_after_emits_strict_inequality(self):
        parsed = parse_filter("created_after=2026-05-01T00:00:00Z")
        assert "posts.created_at > $1" in parsed.where_sql
        assert parsed.params == [
            datetime(2026, 5, 1, tzinfo=timezone.utc),
        ]

    def test_created_before_emits_strict_inequality(self):
        parsed = parse_filter("created_before=2026-05-02T12:34:56Z")
        assert "posts.created_at < $1" in parsed.where_sql
        assert parsed.params == [
            datetime(2026, 5, 2, 12, 34, 56, tzinfo=timezone.utc),
        ]

    def test_niche_filter_uses_content_tasks_subquery(self):
        parsed = parse_filter("niche=ai-ml")
        assert "content_tasks ct" in parsed.where_sql
        assert "ct.niche_slug = $1" in parsed.where_sql
        assert parsed.params == ["ai-ml"]

    def test_author_filter_uses_created_by(self):
        parsed = parse_filter("author=mattg")
        assert "posts.created_by = $1" in parsed.where_sql
        assert parsed.params == ["mattg"]


class TestMultipleClauses:
    def test_and_combines_clauses_in_order(self):
        parsed = parse_filter(
            "state=draft AND created_after=2026-05-01T00:00:00Z",
        )
        # ``AND`` joins each rendered condition.
        assert " AND " in parsed.where_sql
        # Two parameters, in order.
        assert len(parsed.params) == 2
        assert parsed.params[0] == "draft"
        assert parsed.params[1] == datetime(
            2026, 5, 1, tzinfo=timezone.utc,
        )
        # First placeholder is $1, second is $2.
        assert "g.gate_name = $1" in parsed.where_sql
        assert "posts.created_at > $2" in parsed.where_sql

    def test_lowercase_and_separator_works(self):
        parsed = parse_filter("state=draft and author=mattg")
        assert len(parsed.params) == 2
        assert parsed.params == ["draft", "mattg"]

    def test_extra_whitespace_is_tolerated(self):
        parsed = parse_filter("  state=draft   AND   author=mattg  ")
        assert parsed.params == ["draft", "mattg"]


class TestRejection:
    def test_unknown_column_raises(self):
        with pytest.raises(FilterParseError) as e:
            parse_filter("foo=bar")
        assert "unknown filter column" in str(e.value).lower()

    def test_invalid_gate_name_for_state_clause_raises(self):
        with pytest.raises(FilterParseError) as e:
            parse_filter("state=not_a_gate")
        assert "canonical gate name" in str(e.value).lower()

    def test_empty_filter_raises(self):
        with pytest.raises(FilterParseError):
            parse_filter("")
        with pytest.raises(FilterParseError):
            parse_filter("   ")

    def test_clause_without_equals_raises(self):
        with pytest.raises(FilterParseError) as e:
            parse_filter("state")
        assert "key=value" in str(e.value)

    def test_empty_value_raises(self):
        with pytest.raises(FilterParseError) as e:
            parse_filter("state=")
        assert "empty value" in str(e.value).lower()

    def test_value_with_sql_metacharacters_rejected(self):
        # Anything that isn't [A-Za-z0-9_-] is rejected for non-date
        # clauses. Single quotes, semicolons, parens — all out.
        with pytest.raises(FilterParseError):
            parse_filter("author=mattg'; DROP TABLE posts;--")
        with pytest.raises(FilterParseError):
            parse_filter("niche=evil(slug)")

    def test_invalid_iso8601_rejected(self):
        with pytest.raises(FilterParseError) as e:
            parse_filter("created_after=yesterday")
        assert "iso8601" in str(e.value).lower()


class TestNoSqlInjection:
    def test_value_is_never_interpolated_into_sql(self):
        # The whole point of the whitelist + parameterisation: the
        # value never appears literally in the WHERE clause text.
        parsed = parse_filter("author=mattg")
        assert "mattg" not in parsed.where_sql
        assert parsed.params == ["mattg"]

    def test_date_value_is_never_interpolated_into_sql(self):
        parsed = parse_filter("created_after=2026-05-01T00:00:00Z")
        assert "2026" not in parsed.where_sql
