"""Migration 20260507_021346: seed morning_brief job app_settings.

Adds the four tunables ``services.jobs.morning_brief.MorningBriefJob``
reads on every fire. The job posts a daily 7am consolidated digest of
the prior 24h to Discord ops and only pings Telegram when overnight
criticals appear, so the operator (Matt) can wake up to a single
summary instead of scrolling 50+ Captain Hook pings.

Defaults baked in:
- ``morning_brief_enabled`` = true (master switch)
- ``morning_brief_hour_local`` = 7 (informational; cron is in the Job
  class itself — kept here for the dashboard surface)
- ``morning_brief_telegram_critical_only`` = true (route Telegram only
  on critical-severity alerts or failed tasks)
- ``morning_brief_lookback_hours`` = 24

Empty-string sentinel is unused here — every value seeds with a real
default per ``feedback_no_silent_defaults``.
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
                ('morning_brief_enabled', 'true', 'monitoring',
                 'Master switch for the morning_brief scheduled job. When false the job short-circuits and never queries Postgres.',
                 false, true),
                ('morning_brief_hour_local', '7', 'monitoring',
                 'Local-time hour the morning_brief job fires (informational; the active schedule lives in the Job class cron expression). Surfaced here so an operator dashboard can show the configured hour.',
                 false, true),
                ('morning_brief_telegram_critical_only', 'true', 'monitoring',
                 'When true the brief only pings Telegram on critical-severity alerts or failed tasks (Discord still always receives the full brief). Set false to also ping Telegram on quiet mornings.',
                 false, true),
                ('morning_brief_lookback_hours', '24', 'monitoring',
                 'Lookback window in hours used to roll up published posts, awaiting_approval entries, failed tasks, alert counts, costs, and brain probe activity.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260507_021346: applied (4 morning_brief_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'morning_brief_enabled',
                'morning_brief_hour_local',
                'morning_brief_telegram_critical_only',
                'morning_brief_lookback_hours'
            )
            """
        )
        logger.info("Migration 20260507_021346: reverted")
