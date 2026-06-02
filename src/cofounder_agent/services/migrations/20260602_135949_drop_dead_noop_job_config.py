"""Migration: drop config rows for the removed sync_page_views + noop jobs.

Companion to the code removal (#936 cleanup batch B):
  - ``sync_page_views`` — a legacy cloud->local page_views copy from the
    retired two-DB era (``poindexter_brain`` IS prod now), so it could only
    ever no-op. ``sync_cloudflare_analytics`` is the go-forward ingest. The
    job file + its two tests were deleted; this removes its app_settings rows.
  - ``noop`` — the sample no-op job's registration was removed (the sample
    file is retained); this removes its app_settings rows.

Deletes the plugin config + scheduler run/status markers for both. Idempotent
``DELETE`` (0 rows on a fresh DB that never seeded them). ``down()`` is a no-op
— these are config for jobs that no longer exist in code.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    "plugin.job.sync_page_views",
    "plugin.job.noop",
    "plugin_job_last_run_sync_page_views",
    "plugin_job_last_status_sync_page_views",
    "plugin_job_last_run_noop",
    "plugin_job_last_status_noop",
    "idle_last_run_sync_page_views",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info("drop_dead_noop_job_config: removed app_settings rows (%s)", result)


async def down(pool) -> None:
    # No-op: config for jobs that no longer exist in code (sync_page_views +
    # noop). Nothing meaningful to restore.
    logger.info("drop_dead_noop_job_config down: no-op (config for removed jobs)")
