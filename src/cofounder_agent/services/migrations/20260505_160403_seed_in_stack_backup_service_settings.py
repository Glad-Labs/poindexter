"""Migration 20260505_160403: seed in-stack backup service settings

ISSUE: Glad-Labs/poindexter#385 (in-stack DB backup, Tier 1 of the
multi-tier backup story).

Seeds the app_settings keys the new `backup-hourly` / `backup-daily`
compose services read at each tick. All defaults work out of the box —
operators who want to tune cadence or retention edit these rows; no
container restart needed (the loop reads them every tick).

Defaults rationale:
  - hourly cadence + 24-dump retention  → 24h rolling window of fast
    recovery points. Each pg_dump --format=custom is ~395 MB on Matt's
    machine; 24h of headroom = ~9.5 GB. Cheap on any modern SSD.
  - daily cadence + 7-dump retention    → 1-week rolling window for the
    "I broke something Tuesday but only noticed Friday" recovery case.

Failure-alert wiring is implicit (no setting needed): the bash runner
INSERTs into alert_events on any non-zero exit, and the brain daemon's
existing alert_dispatcher poll picks them up. One pipeline.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
                (key, value, category, description, is_secret, is_active)
            VALUES
                ('backup_hourly_enabled', 'true', 'backup',
                 'When false, the backup-hourly container takes no dumps. Loop keeps running so toggling back on is instant.',
                 false, true),
                ('backup_daily_enabled', 'true', 'backup',
                 'When false, the backup-daily container takes no dumps.',
                 false, true),
                ('backup_hourly_interval', '1h', 'backup',
                 'Cadence between hourly dumps. Format: <N>{s|m|h|d}. Read fresh each tick — no restart needed.',
                 false, true),
                ('backup_daily_interval', '24h', 'backup',
                 'Cadence between daily dumps.',
                 false, true),
                ('backup_hourly_retention', '24', 'backup',
                 'Number of hourly dumps to keep. Older dumps are pruned after each successful run.',
                 false, true),
                ('backup_daily_retention', '7', 'backup',
                 'Number of daily dumps to keep.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info("Migration 20260505_160403: applied (6 backup_* settings)")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'backup_hourly_enabled',
                'backup_daily_enabled',
                'backup_hourly_interval',
                'backup_daily_interval',
                'backup_hourly_retention',
                'backup_daily_retention'
            )
            """
        )
        logger.info("Migration 20260505_160403: reverted")
