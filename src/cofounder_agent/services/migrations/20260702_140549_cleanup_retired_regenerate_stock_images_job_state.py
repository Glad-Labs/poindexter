"""Migration 20260702_140549: clean up state for the retired regenerate_stock_images job.

ISSUE: Glad-Labs/glad-labs-stack#2050 (stale-scheduled-jobs triage)

``RegenerateStockImagesJob`` was removed in #1871 (its territory moved to
``media_reconciliation`` — see the "regenerate_stock_images class of bug,
#1904" notes there), but its state outlived it:

  - the ``job_run_state`` row — the metrics exporter repopulates
    ``poindexter_scheduler_job_last_run_age_seconds`` from that table on
    every scrape, so the retired name sat on the System Health
    "Scheduled jobs — freshness" panel with an ever-growing age (~10 days
    when the 2026-07-02 triage caught it), reading like a wedged job.
  - the orphaned ``plugin.job.regenerate_stock_images`` app_settings row —
    config for a job no longer in ``plugins/registry.py``; not seeded by
    ``settings_defaults.py``, so deletion is permanent.

Same shape as ``20260702_054050_cleanup_retired_embedding_job_state``
(the embedding-jobs instance of this exact class). Deleting both rows
makes the ghost gauge series drop out on the next scrape. No-op on fresh
installs, which never had the job.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_JOB = "regenerate_stock_images"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM job_run_state WHERE job_name = $1", _JOB,
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1", f"plugin.job.{_JOB}",
        )
    logger.info(
        "Migration cleanup_retired_regenerate_stock_images_job_state: "
        "removed job_run_state + plugin.job rows for %s", _JOB,
    )


async def down(pool) -> None:
    # One-way: the deleted rows are historical state for a job that no
    # longer exists — there is nothing meaningful to restore.
    del pool
    return
