"""Migration 20260506_052905: seed backup watcher app_settings

ISSUE: Glad-Labs/poindexter#388 (brain backup-watcher with auto-retry).

Seeds the seven tunables ``brain/backup_watcher.py`` reads on every
cycle. Defaults match the in-stack backup container healthcheck
thresholds (90 min hourly, 26 h daily) so the watcher fires at the
exact moment the container is marked unhealthy — no alert-vs-watcher
race window. Auto-retry budget is conservative (2 attempts, 120 s
apart) so a runaway loop can't wedge the backup containers.

All defaults work out of the box; an operator can re-tune via
``poindexter set <key> <value>`` (no daemon restart needed —
``backup_watcher.py`` re-reads each cycle).
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
                ('backup_watcher_enabled', 'true', 'backup',
                 'Master switch for the brain backup-watcher probe (#388). When false, the probe short-circuits without stat-ing dumps or restarting containers.',
                 false, true),
                ('backup_watcher_poll_interval_minutes', '5', 'backup',
                 'Cadence at which the watcher re-checks backup freshness. Matches the brain cycle by default; bump higher only if the brain is overloaded.',
                 false, true),
                ('backup_watcher_hourly_max_age_minutes', '90', 'backup',
                 'Hourly tier staleness threshold. Matches the compose healthcheck so the watcher fires at the same instant the container is marked unhealthy.',
                 false, true),
                ('backup_watcher_daily_max_age_hours', '26', 'backup',
                 'Daily tier staleness threshold (mirrors the compose healthcheck slack of 90 min beyond the 24 h cadence).',
                 false, true),
                ('backup_watcher_max_retries', '2', 'backup',
                 'Consecutive `docker restart` attempts before the watcher gives up and lets the dispatcher page the operator. Cumulative across cycles.',
                 false, true),
                ('backup_watcher_retry_delay_seconds', '120', 'backup',
                 'How long the watcher waits after `docker restart` before re-stat-ing the dump directory. Long enough for postgres reconnect + initial pg_dump to complete on Matt''s machine.',
                 false, true),
                ('backup_watcher_backup_dir', '~/.poindexter/backups/auto', 'backup',
                 'Host path the backup containers bind-mount their dumps into. Override when POINDEXTER_BACKUP_DIR points somewhere non-default (e.g. a second drive).',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260506_052905: applied (7 backup_watcher_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'backup_watcher_enabled',
                'backup_watcher_poll_interval_minutes',
                'backup_watcher_hourly_max_age_minutes',
                'backup_watcher_daily_max_age_hours',
                'backup_watcher_max_retries',
                'backup_watcher_retry_delay_seconds',
                'backup_watcher_backup_dir'
            )
            """
        )
        logger.info("Migration 20260506_052905: reverted")
