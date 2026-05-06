"""Migration 20260506_134100: seed gate pending summary app_settings

ISSUE: Glad-Labs/poindexter#338 (gate-system polish — notification batching).

Seeds the six tunables ``brain/gate_pending_summary_probe.py`` reads on
every cycle. Defaults match the issue spec — hourly poll, 60-minute
grace window, 60-minute Telegram dedup, growth threshold of 3 new
gates before re-paging mid-window, and a low-noise Discord-tier
queue-status emitted every cycle for the spam channel.

Together with the per-flip Telegram demotion in
``services/gates/post_approval_gates.notify_gate_pending`` (now hard-
pinned to ``critical=False`` -> Discord-only), this implements the
"coalesce to 'N posts pending review' once per hour" half of #338's
notification-batching bullet, per Matt's
``feedback_telegram_vs_discord.md`` rule (Telegram = critical alerts
only; Discord = routine progress / per-node updates).

All defaults work out of the box; an operator can re-tune via
``poindexter set <key> <value>`` (no daemon restart needed --
``gate_pending_summary_probe.py`` re-reads each cycle).
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
                ('gate_pending_summary_enabled', 'true', 'gates',
                 'Master switch for the brain gate-pending-summary probe (#338). When false, the probe short-circuits without scanning post_approval_gates and no coalesced summary is sent. The per-flip Discord ping in services/gates/post_approval_gates.notify_gate_pending stays on regardless.',
                 false, true),
                ('gate_pending_summary_poll_interval_minutes', '60', 'gates',
                 'Cadence at which the probe re-scans the pending queue. Hourly per #338 spec. Brain cycle is 5 min so the probe internally rate-limits via this setting (it runs every cycle but no-ops between intervals -- the Discord per-cycle ping uses the inner interval, not this one).',
                 false, true),
                ('gate_pending_summary_min_age_minutes', '60', 'gates',
                 'Grace window after the OLDEST pending gate''s creation before the first Telegram page fires. Prevents paging the operator the instant a gate appears -- they get 60 min to triage on their own before the system escalates.',
                 false, true),
                ('gate_pending_summary_telegram_dedup_minutes', '60', 'gates',
                 'Suppress duplicate Telegram pings within this window when the queue size has not grown past gate_pending_summary_telegram_growth_threshold. Combined with the growth threshold, this prevents re-paging when the queue is the same as last cycle but DOES re-page when N new gates landed.',
                 false, true),
                ('gate_pending_summary_telegram_growth_threshold', '3', 'gates',
                 'Re-fire the coalesced Telegram ping inside the dedup window if the pending queue grew by STRICTLY MORE than this many gates since the last ping. Default 3 means: queue went 5->8 (delta=3, not >3) -> no re-ping; queue went 5->9 (delta=4) -> re-ping. Set to 0 to re-ping on any growth, or to a very large number to disable growth-triggered re-pings entirely.',
                 false, true),
                ('gate_pending_summary_discord_per_cycle', 'true', 'gates',
                 'When true, every probe cycle emits a low-noise Discord ''queue-status'' message (count + oldest age) to the discord_ops channel. Lets the operator monitor the queue in their spam channel without Telegram noise. Disable to make the probe entirely silent until Telegram fires.',
                 false, true)
            ON CONFLICT (key) DO NOTHING
            """
        )
        logger.info(
            "Migration 20260506_134100: applied "
            "(6 gate_pending_summary_* settings)"
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key IN (
                'gate_pending_summary_enabled',
                'gate_pending_summary_poll_interval_minutes',
                'gate_pending_summary_min_age_minutes',
                'gate_pending_summary_telegram_dedup_minutes',
                'gate_pending_summary_telegram_growth_threshold',
                'gate_pending_summary_discord_per_cycle'
            )
            """
        )
        logger.info("Migration 20260506_134100: reverted")
