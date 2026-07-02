"""Migration 20260702_054050: clean up state rows for the retired embedding jobs.

The 2026-06-24 embedding-retention consolidation
(``20260624_004835_embedding_retention_consolidation.py``) retired three
scheduler jobs — ``prune_orphan_embeddings``, ``prune_stale_embeddings`` and
``collapse_old_embeddings`` — in favour of ``retention_policies`` rows served
by the ``embeddings_orphan_prune`` / ``embeddings_collapse`` handlers (all
confirmed live: the policies run under ``run_retention`` and record real
deletions). But the consolidation left the retired jobs' state behind:

  - ``job_run_state`` rows — the metrics exporter repopulates
    ``poindexter_scheduler_job_last_run_age_seconds`` from this table on
    every scrape, so the retired names showed up on the System Health
    "Scheduled jobs — freshness" panel with ever-growing ages (~8 days and
    counting when this was caught), reading like dead daily jobs.

  - ``plugin.job.*`` app_settings rows — orphaned config for jobs that no
    longer exist in ``plugins/registry.py``; not seeded by
    ``settings_defaults.py``, so deletion is permanent.

Deleting both makes the ghost gauge series drop out on the next scrape
(the exporter clears + repopulates from ``job_run_state``). No-ops on
fresh installs, which never had the retired jobs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_RETIRED_JOBS = [
    "prune_orphan_embeddings",
    "prune_stale_embeddings",
    "collapse_old_embeddings",
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for job_name in _RETIRED_JOBS:
            await conn.execute(
                "DELETE FROM job_run_state WHERE job_name = $1",
                job_name,
            )
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1",
                f"plugin.job.{job_name}",
            )
    logger.info(
        "cleanup_retired_embedding_job_state: removed job_run_state + "
        "plugin.job settings for %d retired job(s)",
        len(_RETIRED_JOBS),
    )


async def down(pool) -> None:  # noqa: ARG001
    # One-way deletion — the last-run timestamps and legacy schedule configs
    # are not worth reconstructing; the replacement retention policies carry
    # their own state.
    return
