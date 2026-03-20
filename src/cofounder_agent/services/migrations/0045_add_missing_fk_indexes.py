"""
Migration 0045: Add missing FK indexes on users, user_roles, and settings tables.

Addresses issue #667: 5 FK columns have no supporting index, causing sequential
scans during parent row DELETEs and FK-join queries.

Affected columns:
- users.created_by → users(id)
- users.updated_by → users(id)
- user_roles.assigned_by → users(id)
- settings.created_by → users(id)
- settings.modified_by → users(id)

Note: CREATE INDEX IF NOT EXISTS is used (not CONCURRENTLY) because asyncpg
requires autocommit mode for CONCURRENTLY; IF NOT EXISTS provides idempotency.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_INDEXES = [
    ("users", "created_by", "idx_users_created_by"),
    ("users", "updated_by", "idx_users_updated_by"),
    ("user_roles", "assigned_by", "idx_user_roles_assigned_by"),
    ("settings", "created_by", "idx_settings_created_by"),
    ("settings", "modified_by", "idx_settings_modified_by"),
]


async def _table_has_column(conn, table: str, column: str) -> bool:
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = $1 AND column_name = $2
            """,
            table,
            column,
        )
    )


async def up(pool) -> None:
    """Add FK support indexes."""
    async with pool.acquire() as conn:
        for table, column, idx_name in _INDEXES:
            if not await _table_has_column(conn, table, column):
                logger.debug(f"Column {table}.{column} not found — skipping index {idx_name}")
                continue

            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
            )
            logger.info(f"Ensured index {idx_name} on {table}.{column}")


async def down(pool) -> None:
    """Drop FK support indexes added by this migration."""
    async with pool.acquire() as conn:
        for _, _, idx_name in _INDEXES:
            await conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
    logger.info("Dropped FK support indexes (migration 0045 rollback)")
