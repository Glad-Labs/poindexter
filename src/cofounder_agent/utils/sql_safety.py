"""
SQL Safety Utilities - Prevent SQL injection and ensure query safety

Provides helpers for:
- Parameterized query construction
- SQL identifier validation
- Type-safe query building
- Query logging and auditing
"""

import logging
import re
from typing import Any, List, Tuple, Optional, Dict
from enum import Enum

logger = logging.getLogger(__name__)


class SQLOperator(str, Enum):
    """Safe SQL operators for parameterized queries"""

    EQ = "="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    IN = "IN"
    NOT_IN = "NOT IN"
    LIKE = "LIKE"
    ILIKE = "ILIKE"
    BETWEEN = "BETWEEN"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"


class SQLIdentifierValidator:
    """Validates SQL identifiers (table/column names) to prevent injection"""

    # Allowed pattern: alphanumeric, underscore, no special characters
    IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

    @staticmethod
    def validate(identifier: str, context: str = "identifier") -> bool:
        """
        Validate SQL identifier (table/column name).

        Args:
            identifier: The identifier to validate
            context: Context for logging (e.g., 'table', 'column')

        Returns:
            True if valid, False otherwise

        Note:
            - Must start with letter or underscore
            - Can contain letters, numbers, underscores only
            - Cannot contain special characters or spaces
        """
        if not identifier:
            logger.warning(f"Empty {context} identifier")
            return False

        if not SQLIdentifierValidator.IDENTIFIER_PATTERN.match(identifier):
            logger.warning(f"Invalid {context} identifier: {identifier}")
            return False

        return True

    @staticmethod
    def safe_identifier(identifier: str, context: str = "identifier") -> str:
        """
        Return identifier if valid, raise ValueError otherwise.

        Args:
            identifier: The identifier to validate
            context: Context for error message

        Returns:
            The identifier if valid

        Raises:
            ValueError: If identifier is invalid
        """
        if not SQLIdentifierValidator.validate(identifier, context):
            raise ValueError(f"Invalid {context}: {identifier}")
        return identifier


class ParameterizedQueryBuilder:
    """
    Build parameterized SQL queries safely.

    Example:
        builder = ParameterizedQueryBuilder()
        sql, params = builder.select(
            ["name", "email"],
            "users",
            where_clauses=[("status", "=", "active")],
            limit=10
        )
        # sql: "SELECT name, email FROM users WHERE status = $1 LIMIT $2"
        # params: ["active", 10]
    """

    def __init__(self):
        self.params: List[Any] = []
        self.param_counter = 0

    def add_param(self, value: Any) -> str:
        """
        Add parameter and return placeholder.

        Args:
            value: Parameter value

        Returns:
            Placeholder string (e.g., "$1", "$2")
        """
        self.params.append(value)
        self.param_counter += 1
        return f"${self.param_counter}"

    def select(
        self,
        columns: List[str],
        table: str,
        where_clauses: Optional[List[Tuple[str, SQLOperator, Any]]] = None,
        order_by: Optional[List[Tuple[str, str]]] = None,  # (column, ASC/DESC)
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Tuple[str, List[Any]]:
        """
        Build SELECT query.

        Args:
            columns: List of column names to select
            table: Table name
            where_clauses: List of (column, operator, value) tuples
            order_by: List of (column, direction) tuples
            limit: Result limit
            offset: Result offset

        Returns:
            Tuple of (SQL string, parameters list)
        """
        # Validate identifiers
        table = SQLIdentifierValidator.safe_identifier(table, "table")
        columns = [SQLIdentifierValidator.safe_identifier(c, "column") for c in columns]

        # Build SELECT clause
        columns_str = ", ".join(columns)
        sql = f"SELECT {columns_str} FROM {table}"

        # Add WHERE clause
        if where_clauses:
            where_parts = []
            for column, operator, value in where_clauses:
                column = SQLIdentifierValidator.safe_identifier(column, "column")

                if operator in [SQLOperator.IS_NULL, SQLOperator.IS_NOT_NULL]:
                    where_parts.append(f"{column} {operator.value}")
                else:
                    param = self.add_param(value)
                    where_parts.append(f"{column} {operator.value} {param}")

            sql += " WHERE " + " AND ".join(where_parts)

        # Add ORDER BY
        if order_by:
            order_parts = []
            for column, direction in order_by:
                column = SQLIdentifierValidator.safe_identifier(column, "column")
                direction = direction.upper()
                if direction not in ["ASC", "DESC"]:
                    raise ValueError(f"Invalid sort direction: {direction}")
                order_parts.append(f"{column} {direction}")
            sql += " ORDER BY " + ", ".join(order_parts)

        # Add LIMIT
        if limit is not None:
            param = self.add_param(limit)
            sql += f" LIMIT {param}"

        # Add OFFSET
        if offset is not None:
            param = self.add_param(offset)
            sql += f" OFFSET {param}"

        return sql, self.params

    def insert(
        self,
        table: str,
        columns: Dict[str, Any],
        return_columns: Optional[List[str]] = None,
    ) -> Tuple[str, List[Any]]:
        """
        Build INSERT query.

        Args:
            table: Table name
            columns: Dictionary of {column: value}
            return_columns: Columns to return (optional)

        Returns:
            Tuple of (SQL string, parameters list)
        """
        # Validate identifiers
        table = SQLIdentifierValidator.safe_identifier(table, "table")
        column_names = [SQLIdentifierValidator.safe_identifier(c, "column") for c in columns.keys()]

        # Build INSERT clause
        placeholders = [self.add_param(v) for v in columns.values()]
        columns_str = ", ".join(column_names)
        placeholders_str = ", ".join(placeholders)

        sql = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders_str})"

        # Add RETURNING clause if specified
        if return_columns:
            return_columns = [
                SQLIdentifierValidator.safe_identifier(c, "column") for c in return_columns
            ]
            sql += " RETURNING " + ", ".join(return_columns)

        return sql, self.params

    def update(
        self,
        table: str,
        updates: Dict[str, Any],
        where_clauses: List[Tuple[str, SQLOperator, Any]],
        return_columns: Optional[List[str]] = None,
    ) -> Tuple[str, List[Any]]:
        """
        Build UPDATE query.

        Args:
            table: Table name
            updates: Dictionary of {column: new_value}
            where_clauses: List of (column, operator, value) tuples for WHERE clause
            return_columns: Columns to return (optional)

        Returns:
            Tuple of (SQL string, parameters list)
        """
        # Validate identifiers
        table = SQLIdentifierValidator.safe_identifier(table, "table")

        # Build SET clause
        set_parts = []
        for column, value in updates.items():
            column = SQLIdentifierValidator.safe_identifier(column, "column")
            param = self.add_param(value)
            set_parts.append(f"{column} = {param}")

        sql = f"UPDATE {table} SET " + ", ".join(set_parts)

        # Add WHERE clause
        if where_clauses:
            where_parts = []
            for column, operator, value in where_clauses:
                column = SQLIdentifierValidator.safe_identifier(column, "column")
                param = self.add_param(value)
                where_parts.append(f"{column} {operator.value} {param}")

            sql += " WHERE " + " AND ".join(where_parts)

        # Add RETURNING clause if specified
        if return_columns:
            return_columns = [
                SQLIdentifierValidator.safe_identifier(c, "column") for c in return_columns
            ]
            sql += " RETURNING " + ", ".join(return_columns)

        return sql, self.params

    def delete(
        self,
        table: str,
        where_clauses: List[Tuple[str, SQLOperator, Any]],
    ) -> Tuple[str, List[Any]]:
        """
        Build DELETE query.

        Args:
            table: Table name
            where_clauses: List of (column, operator, value) tuples for WHERE clause

        Returns:
            Tuple of (SQL string, parameters list)
        """
        # Validate identifiers
        table = SQLIdentifierValidator.safe_identifier(table, "table")

        sql = f"DELETE FROM {table}"

        # Add WHERE clause (REQUIRED for safety - no DELETE without WHERE)
        if not where_clauses:
            raise ValueError("DELETE requires WHERE clause for safety")

        where_parts = []
        for column, operator, value in where_clauses:
            column = SQLIdentifierValidator.safe_identifier(column, "column")
            param = self.add_param(value)
            where_parts.append(f"{column} {operator.value} {param}")

        sql += " WHERE " + " AND ".join(where_parts)

        return sql, self.params


# ============================================================================
# Example Usage (in doctest format)
# ============================================================================
"""
Example: Safe SELECT query
    builder = ParameterizedQueryBuilder()
    sql, params = builder.select(
        columns=["id", "name", "email"],
        table="users",
        where_clauses=[
            ("status", SQLOperator.EQ, "active"),
            ("role", SQLOperator.IN, ["admin", "moderator"])
        ],
        order_by=[("created_at", "DESC")],
        limit=10
    )
    # Result:
    # sql: "SELECT id, name, email FROM users WHERE status = $1 AND role IN ($2) ORDER BY created_at DESC LIMIT $3"
    # params: ["active", ["admin", "moderator"], 10]

Example: Safe INSERT query
    builder = ParameterizedQueryBuilder()
    sql, params = builder.insert(
        table="users",
        columns={"name": "John Doe", "email": "john@example.com"},
        return_columns=["id", "created_at"]
    )
    # Result:
    # sql: "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id, created_at"
    # params: ["John Doe", "john@example.com"]

Example: Safe UPDATE query
    builder = ParameterizedQueryBuilder()
    sql, params = builder.update(
        table="users",
        updates={"status": "inactive", "updated_at": "2025-12-30"},
        where_clauses=[("id", SQLOperator.EQ, 123)]
    )
    # Result:
    # sql: "UPDATE users SET status = $1, updated_at = $2 WHERE id = $3"
    # params: ["inactive", "2025-12-30", 123]
"""
