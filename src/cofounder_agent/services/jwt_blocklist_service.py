"""
JWT Blocklist Service — server-side token invalidation.

Provides a PostgreSQL-backed blocklist so that tokens can be revoked
immediately on logout, defeating session replay attacks (issue #721).

Usage
-----
On app startup, call:
    from services.jwt_blocklist_service import jwt_blocklist
    await jwt_blocklist.initialize(pool)

On logout, call:
    await jwt_blocklist.add_token(jti, user_id, expires_at)

On every authenticated request, call:
    if await jwt_blocklist.is_blocked(jti):
        raise 401

Expired rows are automatically purged in cleanup() (call nightly or
at startup after migrations run).
"""

from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


class JWTBlocklistService:
    """
    Stores revoked JWT identifiers (jti) in PostgreSQL.

    Falls back gracefully when the pool is not yet initialized — in that
    case is_blocked() returns False (tokens appear valid), which is safe
    at startup before the DB is ready.
    """

    def __init__(self) -> None:
        self._pool: Any | None = None

    async def initialize(self, pool: Any) -> None:
        """Wire up the asyncpg pool.  Call once after DB is ready."""
        self._pool = pool
        logger.info("[JWTBlocklistService] Initialized with pool")

    @property
    def ready(self) -> bool:
        """True once initialize() has been called with a live pool."""
        return self._pool is not None

    async def add_token(self, jti: str, user_id: str, expires_at: datetime) -> None:
        """
        Record a revoked token.

        Args:
            jti: JWT identifier — from the 'jti' claim, or a SHA-256 hash
                 of the raw token string when no jti claim is present.
            user_id: Owning user's ID string.
            expires_at: When the token naturally expires (for cleanup).
        """
        if not self.ready:
            logger.warning(
                "[JWTBlocklistService] Pool not initialized — cannot blocklist token for user %s",
                user_id,
            )
            return

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO jwt_blocklist (jti, user_id, expires_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (jti) DO NOTHING
                    """,
                    jti,
                    user_id,
                    expires_at,
                )
            logger.info("[JWTBlocklistService] Token blocklisted: jti=%s user_id=%s", jti, user_id)
        except Exception:
            logger.error(
                "[JWTBlocklistService] Failed to blocklist token jti=%s user_id=%s",
                jti,
                user_id,
                exc_info=True,
            )

    async def is_blocked(self, jti: str) -> bool:
        """
        Return True if the given jti is on the blocklist.

        Silently returns False on DB error (fail-open) — tokens will be
        accepted rather than causing false 401s during DB outages.
        """
        if not self.ready:
            return False

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchval(
                    "SELECT 1 FROM jwt_blocklist WHERE jti = $1 LIMIT 1",
                    jti,
                )
            return row is not None
        except Exception:
            logger.error(
                "[JWTBlocklistService] Blocklist check failed for jti=%s — failing open",
                jti,
                exc_info=True,
            )
            return False

    async def cleanup(self) -> int:
        """
        Delete expired blocklist entries.

        Returns the number of rows deleted.  Call this periodically
        (e.g. at startup or via a nightly cron) to prevent unbounded growth.
        """
        if not self.ready:
            return 0

        try:
            now = datetime.now(timezone.utc)
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM jwt_blocklist WHERE expires_at < $1",
                    now,
                )
            # asyncpg returns "DELETE N" — parse the count
            deleted = int(result.split()[-1]) if result else 0
            if deleted:
                logger.info(
                    "[JWTBlocklistService] Cleaned up %d expired blocklist entries", deleted
                )
            return deleted
        except Exception:
            logger.error("[JWTBlocklistService] Cleanup failed", exc_info=True)
            return 0


# Module-level singleton — import and use across routes/middleware.
jwt_blocklist = JWTBlocklistService()
