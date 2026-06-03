"""Migration 20260602_193000_seed_restore_test_settings: seed restore-test probe settings

Seeds the app_settings keys that drive brain/restore_test_probe.py
(Glad-Labs/poindexter#441) — the daily probe that pg_restores the latest
daily dump into a throwaway pgvector container, re-runs the production
migration runner against it, and asserts critical tables survived.

Idempotent: ``ON CONFLICT (key) DO NOTHING`` — never clobbers an
operator-tuned value; a re-run on an up-to-date DB is a no-op. A fresh DB
also gets these keys from ``settings_defaults.seed_all_defaults``; first
writer wins.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Key -> seeded default. Mirrors brain/restore_test_probe.py DEFAULT_* values.
_DEFAULTS = {
    "restore_test_enabled": "true",
    "restore_test_interval_hours": "24",
    "restore_test_backup_dir": "/host-backups/auto",
    "restore_test_tier": "daily",
    "restore_test_postgres_image": "pgvector/pgvector:pg16",
    "restore_test_run_migrations_smoke": "true",
    "restore_test_critical_tables": "posts,app_settings,audit_log",
    "restore_test_min_row_count": "1",
    "restore_test_pg_ready_timeout_seconds": "60",
    "restore_test_restore_timeout_seconds": "300",
    "restore_test_smoke_timeout_seconds": "180",
}


async def up(pool) -> None:
    """Insert each restore-test setting row if absent."""
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            result = await conn.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO NOTHING
                """,
                key,
                value,
            )
            logger.info(
                "Migration seed_restore_test_settings: %s (%s)", key, result,
            )


async def down(pool) -> None:
    """Remove the seeded rows.

    Only deletes a row when it still holds the seeded default — an operator
    who tuned a key keeps their value (the down-migration shouldn't destroy
    operator config).
    """
    async with pool.acquire() as conn:
        for key, value in _DEFAULTS.items():
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1 AND value = $2",
                key,
                value,
            )
        logger.info(
            "Migration seed_restore_test_settings down: removed default rows "
            "(operator-tuned values preserved)"
        )
