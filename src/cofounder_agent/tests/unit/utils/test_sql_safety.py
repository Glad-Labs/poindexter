"""
Unit tests for utils.sql_safety module.

All tests are pure — zero DB or network calls.
Covers SQLIdentifierValidator, ParameterizedQueryBuilder (SELECT/INSERT/UPDATE/DELETE),
and SQLOperator enum.
"""

import pytest

from utils.sql_safety import ParameterizedQueryBuilder, SQLIdentifierValidator, SQLOperator

# ---------------------------------------------------------------------------
# SQLIdentifierValidator.validate
# ---------------------------------------------------------------------------


class TestSQLIdentifierValidatorValidate:
    def test_valid_simple_name(self):
        assert SQLIdentifierValidator.validate("users") is True

    def test_valid_with_underscore(self):
        assert SQLIdentifierValidator.validate("user_id") is True

    def test_valid_with_numbers(self):
        assert SQLIdentifierValidator.validate("table1") is True

    def test_wildcard_is_valid(self):
        assert SQLIdentifierValidator.validate("*") is True

    def test_empty_string_is_invalid(self):
        assert SQLIdentifierValidator.validate("") is False

    def test_starts_with_number_is_invalid(self):
        assert SQLIdentifierValidator.validate("1column") is False

    def test_contains_space_is_invalid(self):
        assert SQLIdentifierValidator.validate("user id") is False

    def test_contains_hyphen_is_invalid(self):
        assert SQLIdentifierValidator.validate("user-id") is False

    def test_sql_injection_attempt_is_invalid(self):
        assert SQLIdentifierValidator.validate("users; DROP TABLE users--") is False

    def test_dot_notation_is_invalid(self):
        assert SQLIdentifierValidator.validate("schema.table") is False


# ---------------------------------------------------------------------------
# SQLIdentifierValidator.safe_identifier
# ---------------------------------------------------------------------------


class TestSQLIdentifierValidatorSafeIdentifier:
    def test_returns_valid_identifier(self):
        result = SQLIdentifierValidator.safe_identifier("users")
        assert result == "users"

    def test_raises_for_invalid_identifier(self):
        with pytest.raises(ValueError, match="Invalid table"):
            SQLIdentifierValidator.safe_identifier("bad-table", "table")

    def test_raises_for_empty_identifier(self):
        with pytest.raises(ValueError):
            SQLIdentifierValidator.safe_identifier("", "column")


# ---------------------------------------------------------------------------
# ParameterizedQueryBuilder — SELECT
# ---------------------------------------------------------------------------


class TestParameterizedQueryBuilderSelect:
    def test_simple_select_all_columns(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(["*"], "users")
        assert "SELECT *" in sql
        assert "FROM users" in sql
        assert params == []

    def test_select_specific_columns(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(["id", "name", "email"], "users")
        assert "id, name, email" in sql
        assert params == []

    def test_select_with_eq_where_clause(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["id", "name"], "users", where_clauses=[("status", SQLOperator.EQ, "active")]
        )
        assert "WHERE" in sql
        assert "status = $1" in sql
        assert params == ["active"]

    def test_select_with_multiple_where_clauses(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["id"],
            "tasks",
            where_clauses=[
                ("status", SQLOperator.EQ, "pending"),
                ("priority", SQLOperator.EQ, "high"),
            ],
        )
        assert "status = $1" in sql
        assert "priority = $2" in sql
        assert params == ["pending", "high"]

    def test_select_with_order_by_asc(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(["id"], "tasks", order_by=[("created_at", "ASC")])
        assert "ORDER BY created_at ASC" in sql

    def test_select_with_order_by_desc(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(["id"], "tasks", order_by=[("updated_at", "DESC")])
        assert "ORDER BY updated_at DESC" in sql

    def test_select_with_invalid_sort_direction_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid sort direction"):
            builder.select(["id"], "tasks", order_by=[("created_at", "SIDEWAYS")])

    def test_select_with_limit(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(["id"], "tasks", limit=10)
        assert "LIMIT $1" in sql
        assert params == [10]

    def test_select_with_offset(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(["id"], "tasks", limit=10, offset=20)
        assert "OFFSET" in sql
        assert 20 in params

    def test_select_with_is_null_operator(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["id"], "tasks", where_clauses=[("deleted_at", SQLOperator.IS_NULL, None)]
        )
        assert "IS NULL" in sql
        assert params == []

    def test_select_with_is_not_null_operator(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["id"], "tasks", where_clauses=[("completed_at", SQLOperator.IS_NOT_NULL, None)]
        )
        assert "IS NOT NULL" in sql

    def test_select_invalid_table_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.select(["id"], "bad-table")

    def test_select_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.select(["bad col"], "users")

    def test_select_with_sql_expression_column(self):
        # Expressions with parens are allowed without identifier validation
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(["COUNT(*) as count"], "tasks")
        assert "COUNT(*) as count" in sql


# ---------------------------------------------------------------------------
# ParameterizedQueryBuilder — INSERT
# ---------------------------------------------------------------------------


class TestParameterizedQueryBuilderInsert:
    def test_simple_insert(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert("users", {"name": "Alice", "email": "alice@example.com"})
        assert "INSERT INTO users" in sql
        assert "VALUES" in sql
        assert "Alice" in params
        assert "alice@example.com" in params

    def test_insert_with_returning(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.insert("users", {"name": "Bob"}, return_columns=["id", "created_at"])
        assert "RETURNING id, created_at" in sql

    def test_insert_placeholders_are_sequential(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert("users", {"name": "Alice", "age": 30})
        assert "$1" in sql
        assert "$2" in sql
        assert len(params) == 2

    def test_insert_invalid_table_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.insert("bad-table", {"col": "val"})

    def test_insert_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.insert("users", {"bad col": "val"})


# ---------------------------------------------------------------------------
# ParameterizedQueryBuilder — UPDATE
# ---------------------------------------------------------------------------


class TestParameterizedQueryBuilderUpdate:
    def test_simple_update(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            "users",
            {"status": "inactive"},
            where_clauses=[("id", SQLOperator.EQ, 42)],
        )
        assert "UPDATE users SET" in sql
        assert "WHERE" in sql
        assert "inactive" in params
        assert 42 in params

    def test_update_multiple_fields(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            "tasks",
            {"status": "done", "priority": "low"},
            where_clauses=[("id", SQLOperator.EQ, "task-1")],
        )
        assert "status = $1" in sql or "priority = $1" in sql
        assert len(params) == 3  # 2 set values + 1 where value

    def test_update_with_returning(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.update(
            "users",
            {"name": "Bob"},
            where_clauses=[("id", SQLOperator.EQ, 1)],
            return_columns=["id", "updated_at"],
        )
        assert "RETURNING id, updated_at" in sql

    def test_update_invalid_table_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.update(
                "bad-table",
                {"col": "val"},
                where_clauses=[("id", SQLOperator.EQ, 1)],
            )


# ---------------------------------------------------------------------------
# ParameterizedQueryBuilder — DELETE
# ---------------------------------------------------------------------------


class TestParameterizedQueryBuilderDelete:
    def test_simple_delete(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete("users", [("id", SQLOperator.EQ, 99)])
        assert "DELETE FROM users" in sql
        assert "WHERE" in sql
        assert 99 in params

    def test_delete_requires_where_clause(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="WHERE clause"):
            builder.delete("users", [])

    def test_delete_multiple_conditions(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete(
            "sessions",
            [("user_id", SQLOperator.EQ, 1), ("expired", SQLOperator.EQ, True)],
        )
        assert "user_id = $1" in sql
        assert "expired = $2" in sql

    def test_delete_invalid_table_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.delete("bad-table", [("id", SQLOperator.EQ, 1)])


# ---------------------------------------------------------------------------
# SQLOperator enum
# ---------------------------------------------------------------------------


class TestSQLOperator:
    def test_eq_value(self):
        assert SQLOperator.EQ == "="

    def test_in_value(self):
        assert SQLOperator.IN == "IN"

    def test_ilike_value(self):
        assert SQLOperator.ILIKE == "ILIKE"

    def test_is_null_value(self):
        assert SQLOperator.IS_NULL == "IS NULL"
