"""Migration 20260606_000000: drop stale idle_last_run_* markers.

ISSUE: Glad-Labs/glad-labs-stack#936 (IdleWorker retirement cleanup)

``idle_last_run_*`` rows in ``app_settings`` were written by the now-retired
IdleWorker (``services/idle_worker.py``). The IdleWorker and its media-gate
driver (``services/jobs/drive_media_gates.py``) are deleted in this PR batch;
these telemetry markers have no writer and no reader. Drop them.

Idempotent: ``DELETE WHERE key LIKE '...'`` no-ops when the matching rows are
already absent (e.g. fresh installs that never ran the old IdleWorker).
``down`` is a no-op — these are stale operational bookmarks with no restore path.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Bulk-delete all idle_last_run_* app_settings rows."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key LIKE 'idle_last_run_%'",
        )
    logger.info("Migration drop_idle_last_run_markers: applied (%s)", result)


async def down(_pool) -> None:
    """No-op — stale IdleWorker markers have no meaningful restore path."""
    logger.info("Migration drop_idle_last_run_markers down: nothing to restore")
