"""
Comprehensive test suite for SQL Safety utilities.

Tests cover:
- SQLIdentifierValidator: injection prevention for table/column names
- ParameterizedQueryBuilder: safe parameterized query construction
- Edge cases and malicious inputs
"""

import pytest
from src.cofounder_agent.utils.sql_safety import (
    SQLIdentifierValidator,
    ParameterizedQueryBuilder,
    SQLOperator,
)


class TestSQLIdentifierValidator:
    """Test SQL identifier validation to prevent injection attacks."""

    # ====== Valid Identifiers ======

    def test_valid_simple_identifier(self):
        """Simple alphanumeric identifier should be valid."""
        assert SQLIdentifierValidator.validate("users") is True
        assert SQLIdentifierValidator.validate("user_id") is True
        assert SQLIdentifierValidator.validate("User123") is True

    def test_valid_underscore_start(self):
        """Identifier starting with underscore should be valid."""
        assert SQLIdentifierValidator.validate("_users") is True
        assert SQLIdentifierValidator.validate("_test_column") is True

    def test_valid_complex_identifier(self):
        """Complex valid identifier with mixed case and underscores."""
        assert SQLIdentifierValidator.validate("user_account_status") is True
        assert SQLIdentifierValidator.validate("TableName123_ABC") is True

    def test_safe_identifier_returns_valid(self):
        """safe_identifier() should return identifier if valid."""
        result = SQLIdentifierValidator.safe_identifier("users")
        assert result == "users"

    # ====== Invalid Identifiers (Injection Attacks) ======

    def test_invalid_empty_identifier(self):
        """Empty identifier should be invalid."""
        assert SQLIdentifierValidator.validate("") is False
        with pytest.raises(ValueError):
            SQLIdentifierValidator.safe_identifier("")

    def test_invalid_numeric_start(self):
        """Identifier starting with number should be invalid."""
        assert SQLIdentifierValidator.validate("123users") is False
        with pytest.raises(ValueError):
            SQLIdentifierValidator.safe_identifier("123users")

    def test_invalid_sql_injection_semicolon(self):
        """Identifier with semicolon (SQL statement separator) should be invalid."""
        assert SQLIdentifierValidator.validate("users; DROP TABLE") is False
        with pytest.raises(ValueError):
            SQLIdentifierValidator.safe_identifier("users; DROP TABLE")

    def test_invalid_sql_injection_quote(self):
        """Identifier with quotes (string delimiter) should be invalid."""
        assert SQLIdentifierValidator.validate("users' OR '1'='1") is False
        assert SQLIdentifierValidator.validate('users" OR "1"="1') is False

    def test_invalid_sql_injection_comment(self):
        """Identifier with SQL comments should be invalid."""
        assert SQLIdentifierValidator.validate("users -- comment") is False
        assert SQLIdentifierValidator.validate("users /* comment */") is False

    def test_invalid_special_characters(self):
        """Identifiers with special characters should be invalid."""
        assert SQLIdentifierValidator.validate("user-table") is False
        assert SQLIdentifierValidator.validate("user.table") is False
        assert SQLIdentifierValidator.validate("user@table") is False
        assert SQLIdentifierValidator.validate("user#table") is False
        assert SQLIdentifierValidator.validate("user$table") is False
        assert SQLIdentifierValidator.validate("user%table") is False
        assert SQLIdentifierValidator.validate("user^table") is False
        assert SQLIdentifierValidator.validate("user&table") is False
        assert SQLIdentifierValidator.validate("user*table") is False
        assert SQLIdentifierValidator.validate("user(table)") is False
        assert SQLIdentifierValidator.validate("user[table]") is False
        assert SQLIdentifierValidator.validate("user{table}") is False

    def test_invalid_spaces(self):
        """Identifiers with spaces should be invalid."""
        assert SQLIdentifierValidator.validate("user table") is False
        assert SQLIdentifierValidator.validate("user  table") is False

    def test_invalid_newline(self):
        """Identifiers with newlines should be invalid."""
        assert SQLIdentifierValidator.validate("users\nDROP") is False

    # ====== Context Parameter ======

    def test_validate_with_context(self):
        """validate() should accept context parameter for logging."""
        assert SQLIdentifierValidator.validate("users", context="table") is True
        assert SQLIdentifierValidator.validate("user_id", context="column") is True

    def test_safe_identifier_with_context(self):
        """safe_identifier() should include context in error message."""
        with pytest.raises(ValueError, match="Invalid table"):
            SQLIdentifierValidator.safe_identifier("123users", context="table")

        with pytest.raises(ValueError, match="Invalid column"):
            SQLIdentifierValidator.safe_identifier("123col", context="column")


class TestParameterizedQueryBuilder:
    """Test parameterized query building for SQL safety."""

    # ====== SELECT Query Tests ======

    def test_select_simple(self):
        """Simple SELECT query with no WHERE clause."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(columns=["id", "name"], table="users")

        assert sql == "SELECT id, name FROM users"
        assert params == []

    def test_select_with_where(self):
        """SELECT with WHERE clause should use parameterized placeholders."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id", "name"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "active")],
        )

        assert sql == "SELECT id, name FROM users WHERE status = $1"
        assert params == ["active"]

    def test_select_multiple_where(self):
        """SELECT with multiple WHERE conditions should chain with AND."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id", "name"],
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "active"),
                ("role", SQLOperator.EQ, "admin"),
            ],
        )

        assert sql == "SELECT id, name FROM users WHERE status = $1 AND role = $2"
        assert params == ["active", "admin"]

    def test_select_with_limit(self):
        """SELECT with LIMIT should parameterize limit value."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            limit=10,
        )

        assert sql == "SELECT id FROM users LIMIT $1"
        assert params == [10]

    def test_select_with_offset(self):
        """SELECT with OFFSET should parameterize offset value."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            offset=20,
        )

        assert sql == "SELECT id FROM users OFFSET $1"
        assert params == [20]

    def test_select_with_order_by(self):
        """SELECT with ORDER BY should validate direction."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id", "name"],
            table="users",
            order_by=[("created_at", "DESC")],
        )

        assert "ORDER BY created_at DESC" in sql
        assert params == []

    def test_select_with_order_by_asc(self):
        """SELECT with ORDER BY ASC should work."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            order_by=[("id", "ASC")],
        )

        assert "ORDER BY id ASC" in sql

    def test_select_with_multiple_order_by(self):
        """SELECT with multiple ORDER BY columns."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            order_by=[("status", "ASC"), ("created_at", "DESC")],
        )

        assert "ORDER BY status ASC, created_at DESC" in sql

    def test_select_invalid_order_direction(self):
        """SELECT with invalid ORDER BY direction should raise ValueError."""
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="Invalid sort direction"):
            builder.select(
                columns=["id"],
                table="users",
                order_by=[("id", "INVALID")],
            )

    def test_select_with_all_options(self):
        """SELECT with all optional parameters."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id", "name", "email"],
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "active"),
                ("role", SQLOperator.EQ, "admin"),
            ],
            order_by=[("created_at", "DESC")],
            limit=10,
            offset=20,
        )

        assert (
            sql
            == "SELECT id, name, email FROM users WHERE status = $1 AND role = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4"
        )
        assert params == ["active", "admin", 10, 20]

    def test_select_is_null_operator(self):
        """SELECT with IS NULL operator should not add parameter."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[("deleted_at", SQLOperator.IS_NULL, None)],
        )

        assert "WHERE deleted_at IS NULL" in sql
        assert params == []

    def test_select_is_not_null_operator(self):
        """SELECT with IS NOT NULL operator should not add parameter."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[("deleted_at", SQLOperator.IS_NOT_NULL, None)],
        )

        assert "WHERE deleted_at IS NOT NULL" in sql
        assert params == []

    # ====== INSERT Query Tests ======

    def test_insert_simple(self):
        """Simple INSERT query."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert(
            table="users",
            columns={"name": "John Doe", "email": "john@example.com"},
        )

        assert "INSERT INTO users" in sql
        assert sql.startswith("INSERT INTO users (")
        assert "VALUES ($1, $2)" in sql or "VALUES ($2, $1)" in sql
        assert set(params) == {"John Doe", "john@example.com"}

    def test_insert_with_returning(self):
        """INSERT with RETURNING clause."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert(
            table="users",
            columns={"name": "John", "email": "john@example.com"},
            return_columns=["id", "created_at"],
        )

        assert "RETURNING id, created_at" in sql
        assert len(params) == 2

    def test_insert_single_column(self):
        """INSERT with single column."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert(
            table="users",
            columns={"name": "John Doe"},
        )

        assert "INSERT INTO users (name)" in sql
        assert "VALUES ($1)" in sql
        assert params == ["John Doe"]

    # ====== UPDATE Query Tests ======

    def test_update_simple(self):
        """Simple UPDATE query with WHERE clause."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            table="users",
            updates={"status": "inactive"},
            where_clauses=[("id", SQLOperator.EQ, 123)],
        )

        assert "UPDATE users SET" in sql
        assert "status = $1" in sql
        assert "WHERE id = $2" in sql
        assert params == ["inactive", 123]

    def test_update_multiple_columns(self):
        """UPDATE multiple columns."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            table="users",
            updates={"status": "inactive", "updated_at": "2025-12-30"},
            where_clauses=[("id", SQLOperator.EQ, 123)],
        )

        assert "UPDATE users SET" in sql
        assert len(params) == 3  # 2 updates + 1 where

    def test_update_with_returning(self):
        """UPDATE with RETURNING clause."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            table="users",
            updates={"status": "active"},
            where_clauses=[("id", SQLOperator.EQ, 123)],
            return_columns=["id", "updated_at"],
        )

        assert "RETURNING id, updated_at" in sql

    def test_update_multiple_where(self):
        """UPDATE with multiple WHERE conditions."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            table="users",
            updates={"status": "active"},
            where_clauses=[
                ("role", SQLOperator.EQ, "admin"),
                ("status", SQLOperator.EQ, "pending"),
            ],
        )

        # SET clause uses $1, WHERE uses $2 and $3
        assert "UPDATE users SET status = $1 WHERE" in sql

    # ====== DELETE Query Tests ======

    def test_delete_simple(self):
        """Simple DELETE query with WHERE clause."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete(
            table="users",
            where_clauses=[("id", SQLOperator.EQ, 123)],
        )

        assert sql == "DELETE FROM users WHERE id = $1"
        assert params == [123]

    def test_delete_multiple_where(self):
        """DELETE with multiple WHERE conditions."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete(
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "inactive"),
                ("deleted_at", SQLOperator.IS_NOT_NULL, None),
            ],
        )

        assert "DELETE FROM users WHERE" in sql
        assert (
            "status = $1 AND deleted_at IS NOT NULL" in sql
            or "deleted_at IS NOT NULL AND status = $1" in sql
        )

    def test_delete_without_where_raises(self):
        """DELETE without WHERE clause should raise ValueError for safety."""
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError, match="DELETE requires WHERE clause"):
            builder.delete(
                table="users",
                where_clauses=[],
            )

    # ====== Injection Attack Prevention ======

    def test_injection_table_name(self):
        """Invalid table name injection should raise ValueError."""
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.select(columns=["id"], table="users; DROP TABLE users;")

    def test_injection_column_name(self):
        """Invalid column name injection should raise ValueError."""
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.select(columns=["id' OR '1'='1"], table="users")

    def test_injection_via_where_values(self):
        """WHERE clause values should be parameterized, not injectable."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "active' OR '1'='1")],
        )

        # The malicious value should be in params, not in SQL
        assert "OR '1'='1" not in sql
        assert params == ["active' OR '1'='1"]

    def test_injection_returning_columns(self):
        """RETURNING column names should be validated."""
        builder = ParameterizedQueryBuilder()
        with pytest.raises(ValueError):
            builder.insert(
                table="users",
                columns={"name": "John"},
                return_columns=["id'; DROP TABLE users; --"],
            )

    # ====== Parameter Placeholder Tests ======

    def test_parameter_counter_increments(self):
        """Parameter placeholders should increment correctly."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "active"),
                ("role", SQLOperator.EQ, "admin"),
                ("created_at", SQLOperator.GT, "2025-01-01"),
            ],
        )

        assert "$1" in sql
        assert "$2" in sql
        assert "$3" in sql
        assert "$4" not in sql  # Should not have $4

    def test_parameter_order_preserved(self):
        """Parameters should be in the same order as they appear in SQL."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "active"),
                ("role", SQLOperator.EQ, "admin"),
            ],
        )

        assert params[0] == "active"
        assert params[1] == "admin"

    # ====== Edge Cases ======

    def test_select_many_columns(self):
        """SELECT with many columns should work."""
        builder = ParameterizedQueryBuilder()
        columns = [f"col_{i}" for i in range(50)]
        sql, params = builder.select(columns=columns, table="users")

        assert sql.count(",") == 49  # n-1 commas for n columns
        assert params == []

    def test_update_many_columns(self):
        """UPDATE many columns simultaneously."""
        builder = ParameterizedQueryBuilder()
        updates = {f"col_{i}": f"value_{i}" for i in range(10)}
        sql, params = builder.update(
            table="users",
            updates=updates,
            where_clauses=[("id", SQLOperator.EQ, 1)],
        )

        assert len(params) == 11  # 10 updates + 1 where

    def test_builder_reuse_not_allowed(self):
        """Builder should not be reused - parameters accumulate."""
        builder = ParameterizedQueryBuilder()

        # First query
        sql1, params1 = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "active")],
        )

        # Second query on same builder - parameters accumulate!
        sql2, params2 = builder.select(
            columns=["id"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "inactive")],
        )

        # Second query should use fresh placeholders
        assert "$2" in sql2  # Because $1 was used in first query
        # This demonstrates why you should create new builders per query

    def test_different_builders_independent(self):
        """Different builder instances should be independent."""
        builder1 = ParameterizedQueryBuilder()
        builder2 = ParameterizedQueryBuilder()

        sql1, params1 = builder1.select(
            columns=["id"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "active")],
        )

        sql2, params2 = builder2.select(
            columns=["id"],
            table="users",
            where_clauses=[("status", SQLOperator.EQ, "inactive")],
        )

        # Both should start from $1
        assert "$1" in sql1
        assert "$1" in sql2

    def test_none_values_as_parameters(self):
        """None values should be handled as parameters (except for IS NULL/IS NOT NULL)."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert(
            table="users",
            columns={"name": "John", "bio": None},
        )

        assert None in params

    # ====== All SQL Operators ======

    def test_all_sql_operators(self):
        """All defined SQLOperator values should be usable."""
        operators = [
            (SQLOperator.EQ, "="),
            (SQLOperator.NE, "!="),
            (SQLOperator.LT, "<"),
            (SQLOperator.LE, "<="),
            (SQLOperator.GT, ">"),
            (SQLOperator.GE, ">="),
            (SQLOperator.IN, "IN"),
            (SQLOperator.NOT_IN, "NOT IN"),
            (SQLOperator.LIKE, "LIKE"),
            (SQLOperator.ILIKE, "ILIKE"),
            (SQLOperator.BETWEEN, "BETWEEN"),
            (SQLOperator.IS_NULL, "IS NULL"),
            (SQLOperator.IS_NOT_NULL, "IS NOT NULL"),
        ]

        for op_enum, op_str in operators:
            builder = ParameterizedQueryBuilder()
            if op_enum in [SQLOperator.IS_NULL, SQLOperator.IS_NOT_NULL]:
                # These don't take a value
                sql, _ = builder.select(
                    columns=["id"],
                    table="users",
                    where_clauses=[("field", op_enum, None)],
                )
                assert op_str in sql
            else:
                sql, params = builder.select(
                    columns=["id"],
                    table="users",
                    where_clauses=[("field", op_enum, "test_value")],
                )
                assert op_str in sql
                assert "test_value" in params


# ============================================================================
# Integration Tests
# ============================================================================


class TestSQLSafetyIntegration:
    """Integration tests combining identifier validation and query building."""

    def test_real_world_user_query(self):
        """Real-world example: Find active admin users paginated."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            columns=["id", "username", "email", "created_at"],
            table="users",
            where_clauses=[
                ("status", SQLOperator.EQ, "active"),
                ("role", SQLOperator.EQ, "admin"),
            ],
            order_by=[("created_at", "DESC")],
            limit=20,
            offset=0,
        )

        assert "SELECT id, username, email, created_at FROM users" in sql
        assert "WHERE status = $1 AND role = $2" in sql or "WHERE role = $1 AND status = $2" in sql
        assert "ORDER BY created_at DESC" in sql
        assert "LIMIT" in sql
        assert "OFFSET" in sql
        assert len(params) == 4  # status, role, limit, offset

    def test_real_world_content_insert(self):
        """Real-world example: Create new content."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.insert(
            table="content",
            columns={
                "title": "My Article",
                "body": "Article content here",
                "author_id": 42,
                "status": "draft",
            },
            return_columns=["id", "created_at", "updated_at"],
        )

        assert "INSERT INTO content" in sql
        assert "RETURNING id, created_at, updated_at" in sql
        assert len(params) == 4

    def test_real_world_task_update(self):
        """Real-world example: Update task status and completion time."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.update(
            table="tasks",
            updates={
                "status": "completed",
                "completed_at": "2025-12-30T10:30:00",
                "result": {"success": True},
            },
            where_clauses=[("id", SQLOperator.EQ, 999)],
            return_columns=["id", "status", "updated_at"],
        )

        assert "UPDATE tasks SET" in sql
        assert "WHERE id = " in sql
        assert "RETURNING" in sql

    def test_real_world_batch_delete(self):
        """Real-world example: Soft delete old temporary records."""
        builder = ParameterizedQueryBuilder()
        sql, params = builder.delete(
            table="temp_records",
            where_clauses=[
                ("created_at", SQLOperator.LT, "2025-01-01"),
                ("status", SQLOperator.EQ, "temporary"),
            ],
        )

        assert "DELETE FROM temp_records WHERE" in sql
        assert len(params) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
