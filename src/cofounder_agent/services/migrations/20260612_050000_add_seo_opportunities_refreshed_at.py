"""Migration: add seo_opportunities.refreshed_at.

SEO Harvest Loop Phase 2c (#763). The outcome-measurement job
(measure_seo_refresh_outcomes) gates "measure N days after the refresh" on a
refresh timestamp. detected_at is bumped every analyzer run (not a refresh
anchor) and outcome_measured_at is the measurement time — neither marks when a
refresh happened. content.republish_post stamps this column alongside the
baseline + status='refreshed'.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ADD_COLUMN = (
    "ALTER TABLE seo_opportunities ADD COLUMN IF NOT EXISTS refreshed_at TIMESTAMPTZ"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_ADD_COLUMN)
    logger.info(
        "Migration add_seo_opportunities_refreshed_at: refreshed_at column added"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE seo_opportunities DROP COLUMN IF EXISTS refreshed_at"
        )
    logger.info("Migration add_seo_opportunities_refreshed_at down: reverted")
