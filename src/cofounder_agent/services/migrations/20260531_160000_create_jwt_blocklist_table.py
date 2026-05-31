"""Migration 20260531_160000_create_jwt_blocklist_table: create jwt_blocklist table

ISSUE: Glad-Labs/poindexter#305 (alert/infra audit follow-up)

``services/jwt_blocklist_service.py`` (issue #721 — server-side token
invalidation / logout revocation) was shipped with usage code but the
``jwt_blocklist`` table itself was never created (lost in the baseline
squash, same class as the ``alert_actions`` gap fixed in
``20260531_005030``). Consequences observed:

- ``JWTBlocklistService.cleanup()`` runs at startup
  (``utils/startup_manager.py`` ~L413) and threw
  ``asyncpg.exceptions.UndefinedTableError: relation "jwt_blocklist"
  does not exist`` on EVERY boot, swallowed into a single
  ``[WARNING] JWT blocklist init failed`` line.
- ``is_blocked()`` fails open (returns False) on that error, so IF the
  revocation path were wired into the request middleware, every revoked
  token would be silently accepted — the feature is structurally dead,
  not merely idle.

This migration creates the table with exactly the columns the service
reads/writes (``add_token`` INSERT, ``is_blocked`` SELECT by ``jti``,
``cleanup`` DELETE by ``expires_at``). ``jti`` is the PRIMARY KEY so the
``ON CONFLICT (jti) DO NOTHING`` in ``add_token`` resolves against it.
Idempotent via ``CREATE TABLE IF NOT EXISTS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent via ``CREATE TABLE IF NOT EXISTS``."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jwt_blocklist (
                jti         TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # cleanup() deletes by expires_at every startup/cron — index it.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jwt_blocklist_expires_at "
            "ON jwt_blocklist (expires_at)"
        )
        logger.info("Migration create_jwt_blocklist_table: applied")


async def down(pool) -> None:
    """Revert: drop the table (the service tolerates its absence — it
    fails open, which is the pre-migration behaviour)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS jwt_blocklist")
        logger.info("Migration create_jwt_blocklist_table down: reverted")
