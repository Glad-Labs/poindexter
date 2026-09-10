"""Migration 20260714_181631_add_cadence_target_posts_per_day_to_niches: add cadence target posts per day to niches

ISSUE: Glad-Labs/poindexter#538

Adds an optional per-niche override for the site-wide
``cadence_slo_expected_posts_per_day`` app_setting that
``brain/health_probes.py::probe_cadence_slo`` pages against. NULL (the
default for every existing niche) means "no override — rely on the
global SLO only"; the probe's per-niche check only runs for niches
where this is set. Purely additive: existing niches are unaffected
until an operator explicitly opts a niche in via
``poindexter topics niche set-cadence``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE niches "
            "ADD COLUMN IF NOT EXISTS cadence_target_posts_per_day numeric"
        )
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS "
            "niches_cadence_target_posts_per_day_check"
        )
        await conn.execute(
            "ALTER TABLE niches ADD CONSTRAINT "
            "niches_cadence_target_posts_per_day_check "
            "CHECK (cadence_target_posts_per_day IS NULL "
            "OR cadence_target_posts_per_day > 0)"
        )
    logger.info(
        "Migration add_cadence_target_posts_per_day_to_niches: applied",
    )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS "
            "niches_cadence_target_posts_per_day_check"
        )
        await conn.execute(
            "ALTER TABLE niches DROP COLUMN IF EXISTS "
            "cadence_target_posts_per_day"
        )
    logger.info(
        "Migration add_cadence_target_posts_per_day_to_niches: reverted",
    )
