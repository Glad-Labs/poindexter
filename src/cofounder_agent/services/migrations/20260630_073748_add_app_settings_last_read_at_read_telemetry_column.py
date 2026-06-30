"""Migration 20260630_073748: add app_settings.last_read_at read-telemetry column.

ISSUE: Glad-Labs/poindexter#756 (settings lifecycle metadata — item 2)

Adds a nullable ``last_read_at TIMESTAMPTZ`` to ``app_settings`` so the
runtime can record which settings are actually consulted. ``SiteConfig.get``
marks each read key in-memory; ``FlushSettingsReadTelemetryJob`` batch-stamps
``last_read_at`` once per minute (throttled to ~1 write/key/hour). A key whose
``last_read_at`` stays NULL well past its ``created_at`` is an orphan candidate —
nothing in the running system reads it — surfaced by
``ProbeZeroReaderSettingsJob`` (item 3) and the Integrations & Admin Grafana
panel.

NULL is the "never observed read" sentinel; the column is intentionally
unindexed (the orphan query scans ~1k rows once an hour, and leaving it
unindexed keeps the per-minute UPDATE a cheap HOT update).

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
Not added to ``0000_baseline.schema.sql``: a baseline only ``CREATE TABLE IF
NOT EXISTS``, so this migration is what adds the column on both fresh installs
(runs after the baseline) and prod (column absent today). The next squash folds
it into the baseline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Add the nullable last_read_at column (idempotent)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ"
        )
    logger.info(
        "add_app_settings_last_read_at up: app_settings.last_read_at ready "
        "(no-op where already present)"
    )


async def down(pool) -> None:
    """Drop the read-telemetry column."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE app_settings DROP COLUMN IF EXISTS last_read_at"
        )
    logger.info("add_app_settings_last_read_at down: dropped app_settings.last_read_at")
