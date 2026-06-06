"""Migration: drop the legacy `logs` table (poindexter#566).

Gitea/admin-logging residue (011_admin_logging_table.sql era). 0 rows, and the
only references — admin_db.add_log_entry/get_logs + their database_service
passthroughs — are deleted in the same PR. Idempotent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS logs")
    logger.info("Migration drop_legacy_logs_table: applied")


async def down(pool) -> None:
    # Best-effort recreate of the minimal shape (the table was unused; this is
    # only so `down` is non-destructive to the migration runner).
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS logs ("
            "id text PRIMARY KEY, agent_name text, level text, message text, "
            "context jsonb, created_at timestamptz DEFAULT NOW())"
        )
    logger.info("Migration drop_legacy_logs_table down: recreated minimal logs table")
