"""
Migration 0047: Fix nullable boolean flags on the users table.

users.is_active, users.is_locked, and users.totp_enabled are currently
nullable with no default value.  A NULL boolean is treated as falsy by
Python but is neither TRUE nor FALSE in SQL, causing:

- Users with NULL is_active treated as inactive (never explicitly deactivated)
- WHERE NOT is_locked excludes NULL-locked users incorrectly
- failed_login_attempts + 1 produces NULL, silently preventing account lockout

Fix:
1. Backfill existing NULLs with safe defaults (is_active→TRUE, others→FALSE/0)
2. Add NOT NULL + DEFAULT constraints to all four columns

NOTE: The users table may be Gitea-managed and some columns (e.g. is_locked,
totp_enabled, failed_login_attempts) may not exist.  Each column is checked
individually so missing columns are silently skipped.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

# Columns to fix: (name, backfill_value, default_value)
_COLUMNS = [
    ("is_active",             "TRUE",  "TRUE"),
    ("is_locked",             "FALSE", "FALSE"),
    ("totp_enabled",          "FALSE", "FALSE"),
    ("failed_login_attempts", "0",     "0"),
]


async def _column_exists(conn, table: str, column: str) -> bool:
    return await conn.fetchval(
        """
        SELECT EXISTS(
            SELECT 1 FROM information_schema.columns
            WHERE table_name = $1 AND column_name = $2
        )
        """,
        table,
        column,
    )


async def run_migration(conn) -> None:
    """Apply migration 0047 — skips columns that don't exist."""
    table_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'users')"
    )
    if not table_exists:
        logger.warning("Table 'users' does not exist — skipping migration 0047")
        return

    for col, backfill, default in _COLUMNS:
        if not await _column_exists(conn, "users", col):
            logger.info(f"users.{col} does not exist — skipping")
            continue

        await conn.execute(f"UPDATE users SET {col} = {backfill} WHERE {col} IS NULL")  # nosec B608  # col + backfill come from _COLUMNS module-level constant
        await conn.execute(f"ALTER TABLE users ALTER COLUMN {col} SET NOT NULL")
        await conn.execute(f"ALTER TABLE users ALTER COLUMN {col} SET DEFAULT {default}")
        logger.info(f"Set NOT NULL + DEFAULT {default} on users.{col}")


async def rollback_migration(conn) -> None:
    """Roll back migration 0047 — skips columns that don't exist."""
    table_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'users')"
    )
    if not table_exists:
        return

    for col, _, _ in _COLUMNS:
        if not await _column_exists(conn, "users", col):
            continue
        await conn.execute(f"ALTER TABLE users ALTER COLUMN {col} DROP NOT NULL")
        await conn.execute(f"ALTER TABLE users ALTER COLUMN {col} DROP DEFAULT")
        logger.info(f"Rolled back NOT NULL + DEFAULT on users.{col}")
