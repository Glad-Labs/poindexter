"""
Migration 0043: Fix posts table TIMESTAMP WITHOUT TIME ZONE columns.

Addresses issue #640: posts.created_at, posts.updated_at, and posts.published_at
use TIMESTAMP WITHOUT TIME ZONE while content_tasks uses TIMESTAMPTZ. This makes
cross-table interval arithmetic produce incorrect results when the database session
timezone differs from UTC.

Fix: Convert all three columns to TIMESTAMP WITH TIME ZONE, reinterpreting stored
values as UTC (the application has always stored UTC values in these columns).

Rollback: Converts back to TIMESTAMP WITHOUT TIME ZONE (data remains UTC).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_COLUMNS = ["created_at", "updated_at", "published_at"]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = $1)",
            table,
        )
    )


async def _column_type(conn, table: str, column: str) -> str:
    return await conn.fetchval(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_name = $1 AND column_name = $2
        """,
        table,
        column,
    )


async def up(pool) -> None:
    """Convert posts timestamp columns from TIMESTAMP to TIMESTAMPTZ."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "posts"):
            logger.warning("Table 'posts' does not exist — skipping migration 0043")
            return

        for col in _COLUMNS:
            current_type = await _column_type(conn, "posts", col)
            if current_type and "without time zone" in current_type.lower():
                await conn.execute(
                    f"""
                    ALTER TABLE posts
                    ALTER COLUMN {col} TYPE TIMESTAMP WITH TIME ZONE
                    USING {col} AT TIME ZONE 'UTC'
                    """
                )
                logger.info(
                    f"Converted posts.{col} from TIMESTAMP WITHOUT TIME ZONE to TIMESTAMPTZ"
                )
            else:
                logger.debug(
                    f"posts.{col} is already {current_type!r} — skipping"
                )


async def down(pool) -> None:
    """Revert posts timestamp columns to TIMESTAMP WITHOUT TIME ZONE."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "posts"):
            return

        for col in _COLUMNS:
            await conn.execute(
                f"""
                ALTER TABLE posts
                ALTER COLUMN {col} TYPE TIMESTAMP WITHOUT TIME ZONE
                USING {col} AT TIME ZONE 'UTC'
                """
            )
        logger.info("Reverted posts timestamp columns to TIMESTAMP WITHOUT TIME ZONE")
