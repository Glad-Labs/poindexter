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
        with pytest.raises(ValueError, match="Invalid column"):
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
        with pytest.raises(ValueError, match="Invalid table"):
            builder.select(["id"], "bad-table")

    def test_select_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
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
        with pytest.raises(ValueError, match="Invalid table"):
            builder.insert("bad-table", {"col": "val"})

    def test_insert_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
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
        with pytest.raises(ValueError, match="Invalid table"):
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
        with pytest.raises(ValueError, match="Invalid table"):
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

    def test_all_operators_present(self):
        expected = {
            "=", "!=", "<", "<=", ">", ">=",
            "IN", "NOT IN", "LIKE", "ILIKE",
            "BETWEEN", "IS NULL", "IS NOT NULL",
        }
        actual = {op.value for op in SQLOperator}
        assert expected == actual

    def test_ne_lt_gt(self):
        assert SQLOperator.NE == "!="
        assert SQLOperator.LT == "<"
        assert SQLOperator.LE == "<="
        assert SQLOperator.GT == ">"
        assert SQLOperator.GE == ">="

    def test_not_in_value(self):
        assert SQLOperator.NOT_IN == "NOT IN"

    def test_like_and_between(self):
        assert SQLOperator.LIKE == "LIKE"
        assert SQLOperator.BETWEEN == "BETWEEN"

    def test_is_not_null_value(self):
        assert SQLOperator.IS_NOT_NULL == "IS NOT NULL"


# ---------------------------------------------------------------------------
# add_param + counter
# ---------------------------------------------------------------------------


class TestAddParam:
    def test_first_param_is_dollar_one(self):
        builder = ParameterizedQueryBuilder()
        ph = builder.add_param("value")
        assert ph == "$1"
        assert builder.params == ["value"]

    def test_sequential_placeholders(self):
        builder = ParameterizedQueryBuilder()
        builder.add_param("a")
        builder.add_param("b")
        ph = builder.add_param("c")
        assert ph == "$3"
        assert builder.params == ["a", "b", "c"]

    def test_param_counter_persists(self):
        builder = ParameterizedQueryBuilder()
        for i in range(5):
            builder.add_param(i)
        assert builder.param_counter == 5
        assert len(builder.params) == 5

    def test_can_store_complex_types(self):
        builder = ParameterizedQueryBuilder()
        builder.add_param([1, 2, 3])
        builder.add_param({"key": "value"})
        builder.add_param(None)
        assert builder.params == [[1, 2, 3], {"key": "value"}, None]


# ---------------------------------------------------------------------------
# SQLIdentifierValidator — additional edge cases
# ---------------------------------------------------------------------------


class TestSQLIdentifierValidatorEdgeCases:
    def test_underscore_only_is_valid(self):
        assert SQLIdentifierValidator.validate("_") is True

    def test_underscore_prefix_is_valid(self):
        assert SQLIdentifierValidator.validate("_private_col") is True

    def test_long_identifier_is_valid(self):
        long_name = "a" * 100
        assert SQLIdentifierValidator.validate(long_name) is True

    def test_uppercase_is_valid(self):
        assert SQLIdentifierValidator.validate("UserId") is True

    def test_mixed_case_is_valid(self):
        assert SQLIdentifierValidator.validate("camelCase_snake") is True

    def test_quoted_identifier_invalid(self):
        assert SQLIdentifierValidator.validate('"users"') is False

    def test_backtick_invalid(self):
        assert SQLIdentifierValidator.validate("`users`") is False

    def test_safe_identifier_default_context(self):
        # Default context arg is "identifier"
        result = SQLIdentifierValidator.safe_identifier("valid_name")
        assert result == "valid_name"

    def test_safe_identifier_error_includes_context(self):
        with pytest.raises(ValueError, match="custom_context"):
            SQLIdentifierValidator.safe_identifier("bad-name", "custom_context")


# ---------------------------------------------------------------------------
# SELECT — additional combinations
# ---------------------------------------------------------------------------


class TestSelectAdvanced:
    def test_select_with_all_clauses(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["id", "name"],
            "users",
            where_clauses=[("status", SQLOperator.EQ, "active")],
            order_by=[("created_at", "DESC")],
            limit=20,
            offset=40,
        )
        assert "SELECT id, name FROM users" in sql
        assert "WHERE status = $1" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT $2" in sql
        assert "OFFSET $3" in sql
        assert params == ["active", 20, 40]

    def test_select_lowercase_order_direction_normalized(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(["id"], "tasks", order_by=[("created_at", "asc")])
        assert "ASC" in sql

    def test_select_column_with_uppercase_AS(self):
        # " AS " should also be detected as expression (it lowercases for matching)
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(["id AS user_id"], "users")
        assert "id AS user_id" in sql

    def test_select_multiple_order_by(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.select(
            ["id"],
            "tasks",
            order_by=[("priority", "DESC"), ("created_at", "ASC")],
        )
        assert "priority DESC" in sql
        assert "created_at ASC" in sql

    def test_select_offset_without_limit(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(["id"], "tasks", offset=100)
        assert "OFFSET $1" in sql
        assert params == [100]
        assert "LIMIT" not in sql

    def test_select_order_by_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.select(["id"], "tasks", order_by=[("bad col", "ASC")])


# ---------------------------------------------------------------------------
# INSERT — additional cases
# ---------------------------------------------------------------------------


class TestInsertAdvanced:
    def test_insert_without_returning(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.insert("users", {"name": "Alice"})
        assert "RETURNING" not in sql

    def test_insert_preserves_column_order(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert("users", {"name": "Alice", "email": "a@b.com", "age": 30})
        # Python dict preserves insertion order in 3.7+
        assert "(name, email, age)" in sql
        assert params == ["Alice", "a@b.com", 30]

    def test_insert_returning_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.insert("users", {"name": "x"}, return_columns=["bad-col"])

    def test_insert_with_none_value(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert("users", {"name": "Alice", "deleted_at": None})
        assert None in params


# ---------------------------------------------------------------------------
# UPDATE — additional cases
# ---------------------------------------------------------------------------


class TestUpdateAdvanced:
    def test_update_invalid_set_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.update(
                "users",
                {"bad col": "val"},
                where_clauses=[("id", SQLOperator.EQ, 1)],
            )

    def test_update_invalid_where_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.update(
                "users",
                {"name": "Bob"},
                where_clauses=[("bad col", SQLOperator.EQ, 1)],
            )

    def test_update_returning_invalid_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.update(
                "users",
                {"name": "x"},
                where_clauses=[("id", SQLOperator.EQ, 1)],
                return_columns=["bad-col"],
            )

    def test_update_param_order(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            "users",
            {"status": "inactive"},
            where_clauses=[("id", SQLOperator.EQ, 42)],
        )
        # SET params come before WHERE params
        assert params == ["inactive", 42]
        assert "status = $1" in sql
        assert "id = $2" in sql


# ---------------------------------------------------------------------------
# DELETE — additional cases
# ---------------------------------------------------------------------------


class TestDeleteAdvanced:
    def test_delete_invalid_where_column_raises(self):
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid column"):
            builder.delete("users", [("bad col", SQLOperator.EQ, 1)])

    def test_delete_param_order(self):
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete(
            "tokens",
            [("user_id", SQLOperator.EQ, "u1"), ("expired", SQLOperator.EQ, True)],
        )
        assert params == ["u1", True]

    def test_delete_uses_and_between_clauses(self):
        builder = ParameterizedQueryBuilder()
        sql, _ = builder.delete(
            "logs",
            [("level", SQLOperator.EQ, "DEBUG"), ("agent", SQLOperator.EQ, "test")],
        )
        assert " AND " in sql
