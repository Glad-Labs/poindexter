"""Migration: drop the orphaned ``drive_media_gates`` plugin-job seed rows.

``drive_media_gates`` was a *planned* media-gate driver job
(``docs/superpowers/plans/2026-05-31-media-gated-publish.md``) whose
implementation never shipped: there is no ``services/jobs/drive_media_gates.py``
and ``plugins/registry.py`` never registered it. Three ``app_settings`` rows
for it were nonetheless seeded by the baseline —

  - ``plugin.job.drive_media_gates``             (PluginScheduler job config)
  - ``plugin_job_last_run_drive_media_gates``    (PluginScheduler telemetry)
  - ``plugin_job_last_status_drive_media_gates`` (PluginScheduler telemetry)

— so the PluginScheduler carried a phantom entry (last fired 2026-06-06) that
can never resolve a job class again. It surfaced as a confusing "dead media
job" when triaging the media pipeline.

The baseline seed file is corrected in lockstep for FRESH installs, but its
``INSERT ... ON CONFLICT (key) DO NOTHING`` never removes an already-seeded
row, so an existing operator's DB keeps the three orphans forever without an
explicit ``DELETE``. This migration is that delete.

Media is now produced by the Stage-2 ``media_pipeline`` lane
(``services/jobs/dispatch_media_pipeline.py`` -> render atoms ->
``services/jobs/media_distribute.py``), with the ``media_reconciliation``
watchdog as the DB<->R2 safety net — none of which is ``drive_media_gates``.

One-way: ``down()`` is an intentional no-op. Re-inserting the rows would
resurrect the same never-runnable phantom this migration exists to remove.

Light imports only (migrations-smoke): logging stdlib.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The three orphaned seeds for the never-implemented drive_media_gates job.
_DEAD_JOB_KEYS = [
    "plugin.job.drive_media_gates",
    "plugin_job_last_run_drive_media_gates",
    "plugin_job_last_status_drive_media_gates",
]


async def up(pool) -> None:
    """Delete the orphaned drive_media_gates seed rows.

    Idempotent: deleting absent rows is a no-op, so this is safe both on a
    fresh DB (where the corrected baseline never seeds them) and on an
    existing DB (where it removes the three phantoms). Cast the array param
    at the call site (``$1::text[]``) per asyncpg's binding rules.
    """
    async with pool.acquire() as conn:
        deleted = await conn.fetch(
            "DELETE FROM app_settings WHERE key = ANY($1::text[]) RETURNING key",
            _DEAD_JOB_KEYS,
        )
    logger.info(
        "drop_orphaned_drive_media_gates_job_seed: deleted %d/%d orphaned row(s)",
        len(deleted),
        len(_DEAD_JOB_KEYS),
    )


async def down(pool) -> None:
    """Intentional no-op (one-way migration).

    Re-inserting the rows would recreate the never-runnable phantom job that
    this migration removes, so there is nothing meaningful to restore.
    """
    logger.warning(
        "drop_orphaned_drive_media_gates_job_seed down: no-op "
        "(refusing to resurrect a dead-job seed)"
    )
