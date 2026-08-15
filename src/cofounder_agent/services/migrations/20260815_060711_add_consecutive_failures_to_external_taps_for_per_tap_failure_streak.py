"""Migration 20260815_060711: add ``external_taps.consecutive_failures``.

ISSUE: Glad-Labs/poindexter#1015 — external_taps failures all shared one
findings dedup key (``job-fail:run_taps``), so a chronically failing tap
masked every other tap breaking.

The fix emits a per-tap ``tap_failure`` finding, and escalates its severity
from ``info`` (dashboard-visible, never routed) to ``warn`` (routed) only
once a tap has failed ``tap_failure_alert_after_consecutive`` times in a
row — so a single transient blip (the 2026-08-15 dev.to container-DNS
``ConnectError``, which self-healed on the next run) records without paging,
while a sustained outage still pages.

That needs a real streak counter. ``last_run_status`` alone is one bit —
enough to distinguish "first failure" from "second", but not to support an
operator-configurable threshold of 3+. This column carries the count:
incremented on a genuine failure, reset to 0 on success, and deliberately
LEFT ALONE on a deferral (``GpuBusyError`` / ``GpuLockTimeoutError``), which
is the GPU scheduler correctly declining work rather than the tap breaking.

Idempotent via ``ADD COLUMN IF NOT EXISTS``. Existing rows backfill to 0,
which reads as "no known streak" — a tap that is currently failing simply
takes one more run to reach the alert threshold. That is the right cold-start
behaviour: it cannot page retroactively for history it never observed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Add the failure-streak counter to external_taps."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            ALTER TABLE external_taps
              ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0
            """
        )
    logger.info(
        "[migration] external_taps.consecutive_failures added "
        "(existing rows backfilled to 0)"
    )
