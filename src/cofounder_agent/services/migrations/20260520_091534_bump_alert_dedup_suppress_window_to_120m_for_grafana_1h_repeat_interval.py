"""Bump ``alert_repeat_suppress_window_minutes`` 30 → 120 (finding #499).

Captured 2026-05-20: `Content Quality Drop` fired 5x in 3.5h (00:44,
01:46, 02:51, 03:56, 04:06 UTC), all same Grafana fingerprint, all
``dispatch_result='sent'``. The dedup state correctly recorded each
fire as ``repeat_count=1`` — meaning the reset branch tripped on each
arrival, not the suppress branch.

``brain.alert_dispatcher._evaluate_dedup_decision`` uses
``age_since_last_seen >= suppress_window_min`` to detect "this is a
fresh fire". Grafana's default ``repeat_interval`` is 1h, so every
webhook for an ongoing FIRING alert arrives ~60min after the prior
``last_seen_at`` — always outside the 30-min window. Reset trips,
operator gets re-paged on every Grafana cycle.

Issue #499 lists three fix paths. Path 1 (use ``age_since_first_seen``)
doesn't actually fix the bug: ``first_seen`` also grows past any fixed
window for ongoing alerts. Path 2 (track upstream RESOLVED transitions)
is more semantic but a real lift. Path 3 (this migration) is the
correct band-aid: bump the window past Grafana's repeat_interval so
the reset branch only trips on a genuine quiet gap.

120 min was chosen as 2x Grafana's 1h default — leaves slack for
clock skew, restart-recovery dispatch lag, and the brain's own 5-min
probe cycle. The semantic fix (path 2) tracks as a follow-up.

Idempotent — the UPDATE is a no-op if the operator has already set
the value higher; ``WHERE value = '30'`` guards against clobbering a
deliberate operator override.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE app_settings
            SET value = '120',
                description = description || ' [bumped 2026-05-20 from 30 to 120 per finding #499 — Grafana''s 1h repeat_interval was tripping the reset branch every cycle and re-paging on ongoing alerts]',
                updated_at = NOW()
            WHERE key = 'alert_repeat_suppress_window_minutes'
              AND value = '30'
            """,
        )
        logger.info(
            "20260520_091534: alert_repeat_suppress_window_minutes bump "
            "30 → 120 (%s)",
            result,
        )


async def down(pool) -> None:
    """Restore the 30-min default. Useful only if the new value
    surfaces a different bug (e.g. ongoing alerts that should re-page
    every hour by design, like long-running outages)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = '30' "
            "WHERE key = 'alert_repeat_suppress_window_minutes' "
            "AND value = '120'",
        )
        logger.info("20260520_091534 down: restored 30-min window")
