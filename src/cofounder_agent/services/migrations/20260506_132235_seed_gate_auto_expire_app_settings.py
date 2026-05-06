"""Migration 20260506_132235: seed gate auto expire app_settings

ISSUE: Glad-Labs/poindexter#338 — gate system polish, "auto-expire pending
gates" bullet.

Seeds the five tunables ``brain/gate_auto_expire_probe.py`` reads on every
cycle. Defaults: 7-day max age before auto-rejection (per the issue's
suggested default), 30-min poll cadence (stale gates aren't time-sensitive,
sparse polling is fine), batch cap of 50 to avoid huge expiry waves, and a
notify threshold of 1 so any auto-expiry produces a coalesced operator ping.
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
                ('gate_auto_expire_enabled', 'true', 'gates',
                 'Master switch for the brain gate auto-expire probe (#338). When false, the probe short-circuits without scanning gates.',
                 false, true),
                ('gate_pending_max_age_hours', '168', 'gates',
                 'Pending gates older than this many hours get auto-rejected with a sentinel reason. Default 168h = 7 days, per the #338 spec — operator should set lower if they want stricter SLA.',
                 false, true),
                ('gate_auto_expire_poll_interval_minutes', '30', 'gates',
                 'Cadence at which the brain runs the auto-expire probe. Stale gates aren''t time-sensitive, so 30-min default is sparse on purpose.',
                 false, true),
                ('gate_auto_expire_batch_size', '50', 'gates',
                 'Cap per-cycle expiry to this many gates to avoid huge batches. Excess rolls over to the next cycle.',
                 false, true),
                ('gate_auto_expire_notify_threshold', '1', 'gates',
                 'Only ping the operator (Telegram coalesced) when batch size >= this. Default 1 = always notify on any expiry.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260506_132235: applied (5 gate_auto_expire_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'gate_auto_expire_enabled',
                'gate_pending_max_age_hours',
                'gate_auto_expire_poll_interval_minutes',
                'gate_auto_expire_batch_size',
                'gate_auto_expire_notify_threshold'
            )
            """
        )
        logger.info("Migration 20260506_132235: reverted")
