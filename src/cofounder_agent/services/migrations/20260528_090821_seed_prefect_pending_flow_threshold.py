"""Seed app_settings default for the PENDING/Submitting threshold on
the Prefect stuck-flow probe.

The original probe (migration ``20260526_135306``) only watched
state=RUNNING runs. Captured 2026-05-25 → 2026-05-27: a single
``content_generation`` flow run (smoky-chowchow) sat in
state=PENDING / Submitting for 50+ hours, holding the work pool's
concurrency slot. The worker reported ONLINE and polled every 5s
but never claimed a new SCHEDULED run because the slot was taken
by the stranded PENDING entry. See Glad-Labs/poindexter#518.

This migration adds the dedicated PENDING threshold so the
operator can tune it independently of the RUNNING threshold
(PENDING normally completes in seconds, RUNNING normally takes
~5min, so the conservative thresholds are correspondingly
different — 5min for PENDING vs. 30min for RUNNING).

``ON CONFLICT DO NOTHING`` — re-runnable, never overwrites
operator-tuned values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, category, is_secret)
            VALUES
              (
                'prefect_stuck_flow_pending_threshold_minutes', '5',
                'A flow run that has been PENDING/Submitting longer '
                'than this is considered stranded. Captured 2026-05-25: '
                'a PENDING run sat 50+ hours and blocked the work '
                'pool''s concurrency slot. Typical submit completes in '
                'seconds; default 5m is conservative. Tune downward '
                'for tighter detection; tune upward only if you ever '
                'have legitimately long submit handshakes.',
                'brain-probes', false
              )
            ON CONFLICT (key) DO NOTHING;
            """
        )
        logger.info(
            "Migration seed_prefect_pending_flow_threshold: applied",
        )


async def down(pool) -> None:
    """Remove the seeded row. Safe because operators who tuned the
    value away from the default would still get the default from the
    probe code itself on the next cycle."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = 'prefect_stuck_flow_pending_threshold_minutes';
            """
        )
        logger.info(
            "Migration seed_prefect_pending_flow_threshold down: reverted",
        )
