"""Migration 20260708_043000: add the ``clock_skew_samples`` time-series table.

Backs ``brain/clock_skew_probe.py`` (2026-07-08 investigation — a transient
WSL2 ``CLOCK_REALTIME`` excursion left ``poindexter-postgres-local`` ~4h in the
future for a window, silently poisoning every DB timestamp). The brain is
headless (no ``/metrics`` endpoint), so the continuous skew signal reaches
Grafana through Postgres — the same way ``gpu_metrics`` / ``cost_logs`` do. The
probe appends one row per 5-min cycle (``status`` in ``ok`` / ``skewed`` /
``unknown``; ``skew_seconds`` NULL when ``unknown``) and self-prunes rows older
than ``app_settings.clock_skew_sample_retention_days`` (default 30), so the
table stays ~288 rows/day with no ``retention_policies`` dependency.

New post-baseline table — fresh installs run this after the Phase F baseline;
prod runs it once. ``CREATE TABLE IF NOT EXISTS`` is idempotent.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clock_skew_samples (
                id            BIGSERIAL PRIMARY KEY,
                sampled_at    timestamptz NOT NULL DEFAULT now(),
                skew_seconds  double precision,
                reference_url text,
                status        text NOT NULL
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clock_skew_samples_sampled_at "
            "ON clock_skew_samples (sampled_at DESC)"
        )
    logger.info(
        "add_clock_skew_samples up: created clock_skew_samples + "
        "idx_clock_skew_samples_sampled_at (no-op where already present)"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS clock_skew_samples")
    logger.info("add_clock_skew_samples down: dropped clock_skew_samples")
