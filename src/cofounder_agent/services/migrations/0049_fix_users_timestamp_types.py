"""
Migration 0049: Convert users table TIMESTAMP columns to TIMESTAMPTZ.

Addresses issue #863: users table uses TIMESTAMP WITHOUT TIME ZONE columns.
Python's timezone-aware datetimes (datetime.now(timezone.utc)) are rejected by
PostgreSQL when the column type is TIMESTAMP WITHOUT TIME ZONE.

Migration 0043 fixed the same issue on the posts table; this applies the same
fix to the users table.

Changes:
- Convert users.created_at from TIMESTAMP to TIMESTAMPTZ
- Convert users.updated_at from TIMESTAMP to TIMESTAMPTZ
- Convert users.last_login from TIMESTAMP to TIMESTAMPTZ (if exists)

Uses AT TIME ZONE 'UTC' to preserve existing values during conversion.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_COLUMNS = ["created_at", "updated_at", "last_login"]


async def up(pool) -> None:
    """Convert users timestamp columns to TIMESTAMPTZ."""
    async with pool.acquire() as conn:
        # Check table exists
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'users')"
        )
        if not exists:
            logger.warning("Table 'users' does not exist — skipping")
            return

        for col in _COLUMNS:
            # Check if column exists
            col_exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = $1)",
                col,
            )
            if not col_exists:
                logger.debug(f"Column users.{col} does not exist — skipping")
                continue

            # Check current type
            current_type = await conn.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = $1",
                col,
            )
            if current_type == "timestamp with time zone":
                logger.debug(f"users.{col} already TIMESTAMPTZ — skipping")
                continue

            await conn.execute(
                f"ALTER TABLE users "
                f"ALTER COLUMN {col} TYPE TIMESTAMPTZ "
                f"USING {col} AT TIME ZONE 'UTC'"
            )
            logger.info(f"Converted users.{col} from {current_type} to TIMESTAMPTZ")


async def down(pool) -> None:
    """Revert users timestamp columns to TIMESTAMP WITHOUT TIME ZONE."""
    async with pool.acquire() as conn:
        for col in _COLUMNS:
            col_exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = $1)",
                col,
            )
            if not col_exists:
                continue

            await conn.execute(
                f"ALTER TABLE users "
                f"ALTER COLUMN {col} TYPE TIMESTAMP WITHOUT TIME ZONE"
            )
            logger.info(f"Reverted users.{col} to TIMESTAMP WITHOUT TIME ZONE")
