"""
Migration 0042: Create JWT blocklist table for server-side logout.

Issue #721: JWT logout was a stub — tokens remained valid after logout,
enabling session replay. This migration creates the jwt_blocklist table
used by JWTBlocklistService to invalidate tokens on logout.

Changes:
1. CREATE TABLE jwt_blocklist (jti, user_id, expires_at, blocklisted_at)
2. CREATE INDEX idx_jwt_blocklist_expires on expires_at for cleanup queries
3. CREATE INDEX idx_jwt_blocklist_user_id on user_id for user-scoped revocation

Rollback: drop the table and indexes.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    """Create jwt_blocklist table and indexes."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jwt_blocklist (
                jti         TEXT        PRIMARY KEY,
                user_id     TEXT        NOT NULL,
                expires_at  TIMESTAMPTZ NOT NULL,
                blocklisted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        logger.info("Table jwt_blocklist ensured")

        # Index for efficient cleanup of expired rows (nightly purge)
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jwt_blocklist_expires
                ON jwt_blocklist (expires_at)
            """
        )
        logger.info("Index idx_jwt_blocklist_expires ensured")

        # Index for user-scoped revocation (e.g. revoke all tokens for a user)
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jwt_blocklist_user_id
                ON jwt_blocklist (user_id)
            """
        )
        logger.info("Index idx_jwt_blocklist_user_id ensured")


async def down(pool) -> None:
    """Drop the jwt_blocklist table and its indexes."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS jwt_blocklist CASCADE")
        logger.info("Rolled back 0042: dropped jwt_blocklist table")
