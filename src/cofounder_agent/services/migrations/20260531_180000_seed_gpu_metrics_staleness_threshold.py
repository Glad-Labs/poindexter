"""Migration 20260531_180000: seed gpu_metrics_staleness_threshold_minutes

ISSUE: Glad-Labs/poindexter#536

The brain ``probe_gpu_temperature`` now distinguishes "exporter alive" from
"writing fresh data": if the newest ``gpu_metrics`` row is older than this
threshold the probe fails (a frozen feed means GPU monitoring is blind),
instead of reading a stale normal-temperature row and falsely reporting
healthy. The probe reads this key with a code-default of 15; seed it so the
threshold is visible + tunable in the config plane (default 15 min ≈ 3× the
5-min brain cycle, generous against transient scrape gaps).

Idempotent via ``ON CONFLICT DO NOTHING`` so an operator-tuned value is kept.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO NOTHING",
            "gpu_metrics_staleness_threshold_minutes", "15",
        )
    logger.info("Migration seed_gpu_metrics_staleness_threshold: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings "
            "WHERE key = 'gpu_metrics_staleness_threshold_minutes' AND value = '15'"
        )
