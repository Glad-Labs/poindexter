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

import asyncpg

from services.logger_config import get_logger

logger = get_logger(__name__)


class JWTBlocklistService:
    """
    Stores revoked JWT identifiers (jti) in PostgreSQL.

    Falls back gracefully when the pool is not yet initialized — in that
    case is_blocked() returns False (tokens appear valid), which is safe
    at startup before the DB is ready.

    Failure posture (see #305): fail-open is preserved for *transient* DB
    errors (a blip must not 401 every authenticated user), but a *missing
    table* — a permanent structural defect that silently disables
    revocation forever — is escalated as a critical finding so it surfaces
    in the alert pipeline instead of hiding behind a single warning line.
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    @staticmethod
    def _escalate_missing_table(op: str) -> None:
        """The jwt_blocklist table is gone — revocation is non-functional.

        Emit a deduped critical finding (routes to Telegram via the findings
        pipeline). Fire-and-forget; never raises. Imported lazily to avoid an
        import cycle (findings -> audit_log) at service-load time.
        """
        try:
            from utils.findings import emit_finding

            emit_finding(
                source="jwt_blocklist_service",
                kind="missing_table",
                title="jwt_blocklist table missing — token revocation is DEAD",
                body=(
                    f"JWTBlocklistService.{op}() hit "
                    "asyncpg.UndefinedTableError: the `jwt_blocklist` table does "
                    "not exist. Server-side token revocation (#721) is "
                    "non-functional and is_blocked() is failing open — any "
                    "revoked/logged-out JWT will be accepted. Run pending "
                    "migrations (20260531_160000_create_jwt_blocklist_table)."
                ),
                severity="critical",
                dedup_key="jwt-blocklist:missing-table",
            )
        except Exception:
            logger.error(
                "[JWTBlocklistService] failed to emit missing-table finding",
                exc_info=True,
            )

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
        pool = self._pool
        if pool is None:
            logger.warning(
                "[JWTBlocklistService] Pool not initialized — cannot blocklist token for user %s",
                user_id,
            )
            return

        try:
            async with pool.acquire() as conn:
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
        except asyncpg.exceptions.UndefinedTableError:
            # Structural defect, not a transient blip: a revoke that silently
            # no-ops means a logged-out token stays valid. Make it loud.
            self._escalate_missing_table("add_token")
            logger.error(
                "[JWTBlocklistService] Failed to blocklist token jti=%s user_id=%s "
                "(jwt_blocklist table missing)",
                jti,
                user_id,
            )
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
        pool = self._pool
        if pool is None:
            return False

        try:
            async with pool.acquire() as conn:
                row = await conn.fetchval(
                    "SELECT 1 FROM jwt_blocklist WHERE jti = $1 LIMIT 1",
                    jti,
                )
            return row is not None
        except asyncpg.exceptions.UndefinedTableError:
            # Missing table = revocation permanently dead, not a transient
            # blip. Still fail open (don't 401 everyone), but make it loud.
            self._escalate_missing_table("is_blocked")
            logger.error(
                "[JWTBlocklistService] Blocklist check failed for jti=%s — "
                "jwt_blocklist table missing, failing open (revocation DEAD)",
                jti,
            )
            return False
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
        pool = self._pool
        if pool is None:
            return 0

        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
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
        except asyncpg.exceptions.UndefinedTableError:
            self._escalate_missing_table("cleanup")
            logger.error(
                "[JWTBlocklistService] Cleanup failed — jwt_blocklist table missing"
            )
            return 0
        except Exception:
            logger.error("[JWTBlocklistService] Cleanup failed", exc_info=True)
            return 0


# Module-level singleton — import and use across routes/middleware.
jwt_blocklist = JWTBlocklistService()
