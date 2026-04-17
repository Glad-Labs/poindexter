"""
Migration 0051: Fix settings.modified_at TIMESTAMP → TIMESTAMPTZ.

Addresses issue #998: settings.modified_at was created as TIMESTAMP WITHOUT
TIME ZONE in migration 0023, while every other table uses TIMESTAMPTZ.
This causes incorrect interval arithmetic when the database session timezone
differs from UTC.

Fix: Convert to TIMESTAMP WITH TIME ZONE, reinterpreting stored values as UTC
(the application has always stored UTC values in this column).

Rollback: Converts back to TIMESTAMP WITHOUT TIME ZONE (data remains UTC).
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


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
    """Convert settings.modified_at from TIMESTAMP to TIMESTAMPTZ."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "settings"):
            logger.warning("Table 'settings' does not exist — skipping migration 0051")
            return

        current_type = await _column_type(conn, "settings", "modified_at")
        if current_type and "without time zone" in current_type.lower():
            await conn.execute(
                """
                ALTER TABLE settings
                ALTER COLUMN modified_at TYPE TIMESTAMP WITH TIME ZONE
                USING modified_at AT TIME ZONE 'UTC'
                """
            )
            logger.info(
                "Converted settings.modified_at from TIMESTAMP WITHOUT TIME ZONE to TIMESTAMPTZ"
            )
        else:
            logger.debug(
                f"settings.modified_at is already {current_type!r} — skipping"
            )


async def down(pool) -> None:
    """Revert settings.modified_at to TIMESTAMP WITHOUT TIME ZONE."""
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "settings"):
            return

        await conn.execute(
            """
            ALTER TABLE settings
            ALTER COLUMN modified_at TYPE TIMESTAMP WITHOUT TIME ZONE
            USING modified_at AT TIME ZONE 'UTC'
            """
        )
        logger.info("Reverted settings.modified_at to TIMESTAMP WITHOUT TIME ZONE")
