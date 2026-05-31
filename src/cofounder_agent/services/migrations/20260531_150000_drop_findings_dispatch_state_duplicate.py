"""Migration 20260531_150000: drop findings_dispatch_state (duplicate path)

Reverts the schema half of the #461 brain findings_dispatcher. That dispatcher
was a parallel reinvention of the EXISTING ``findings_alert_router`` worker job
(landed 2026-05-15), which already bridges ``audit_log`` findings ->
``alert_events`` -> ``alert_dispatcher`` (with dedup + the severity matrix).
Running both risked double-delivering every warn/critical finding, so the brain
dispatcher + its ``findings_dispatch_state`` tracking table were removed.

The ``findings.<kind>.*`` app_settings policies are intentionally KEPT — they
are the genuinely-new value (per-kind delivery/suppression) and are slated to
be wired into ``findings_alert_router`` (so e.g. ``media_drift`` can be
suppressed from paging) rather than driving a separate delivery path.

Idempotent: ``DROP TABLE IF EXISTS``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS findings_dispatch_state")
    logger.info(
        "Migration drop_findings_dispatch_state: dropped the duplicate-path "
        "tracking table (findings_alert_router + alert_dispatcher is canonical)"
    )


async def down(pool) -> None:
    """Recreate the table shell (without the backfill) for reversibility."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS findings_dispatch_state (
                finding_id      BIGINT PRIMARY KEY,
                kind            TEXT NOT NULL,
                dedup_key       TEXT,
                channel         TEXT NOT NULL,
                dispatch_result TEXT NOT NULL,
                dispatched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
