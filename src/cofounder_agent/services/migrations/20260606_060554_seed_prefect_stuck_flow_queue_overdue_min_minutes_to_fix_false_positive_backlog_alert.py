"""Seed prefect_stuck_flow_queue_overdue_min_minutes — fix false-positive backlog alert.

The Prefect stuck-flow probe's queue-backlog detector counts every SCHEDULED
run whose expected start time has passed (overdue > 0 minutes). With a
2-minute cron interval and each content_generation run completing in ~5-9s,
Prefect's scheduler queues several "overdue" SCHEDULED runs that are only
1-2 minutes past their start time — normal scheduling jitter, not a real
backlog. This caused a false-positive "Prefect queue backlog: 10 overdue
scheduled run(s)" alert when the system was healthy and the pipeline queue
was empty.

The fix: only count a SCHEDULED run as overdue once it has been waiting for
at least ``prefect_stuck_flow_queue_overdue_min_minutes`` (default 5). Five
minutes is safely above normal scheduling jitter (runs clear in <10 seconds)
and well below the true-incident range where the slot is blocked for 30+
minutes. This matches the ``prefect_stuck_flow_pending_threshold_minutes``
default and keeps the three thresholds (running/pending/scheduled-min) in
the same order of magnitude.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, is_secret)
            VALUES (
                'prefect_stuck_flow_queue_overdue_min_minutes',
                '5',
                'Minimum minutes a SCHEDULED Prefect run must be overdue before it '
                'counts toward the queue-depth backlog threshold. Prevents false-positive '
                'alerts when normal scheduling jitter causes runs to be overdue by 1-2 '
                'minutes (each content_generation run completes in ~5-9 seconds on an '
                'empty queue). Set lower to detect backlogs faster; set higher to '
                'tolerate more jitter. Pairs with prefect_stuck_flow_queue_depth_threshold.',
                false
            )
            ON CONFLICT (key) DO NOTHING;
            """
        )
        logger.info(
            "Migration applied: seeded prefect_stuck_flow_queue_overdue_min_minutes=5"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = 'prefect_stuck_flow_queue_overdue_min_minutes';
            """
        )
        logger.info(
            "Migration reverted: removed prefect_stuck_flow_queue_overdue_min_minutes"
        )
