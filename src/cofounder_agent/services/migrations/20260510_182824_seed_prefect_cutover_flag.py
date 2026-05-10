"""Seed the Prefect cutover flag (#410 Phase 0).

Adds ``app_settings.use_prefect_orchestration`` (default ``'false'``).
The TaskExecutor checks this every poll cycle — when ``'true'`` it
becomes a no-op poller and Prefect's deployment owns dispatch
entirely. Default ``'false'`` preserves today's behavior so this
migration ships safely on every install.

Cutover sequence (mirrors Lane C's pattern):
1. **Phase 0** (this migration) — flow + deployment shipped, flag
   defaults to ``'false'``. Both daemons can run side-by-side without
   double-claiming because the TaskExecutor short-circuits when the
   flag is on.
2. **Phase 1** — operator flips the flag to ``'true'``. Watch 24-48h.
3. **Phase 2** — flip the seed default to ``'true'`` so new installs
   get Prefect by default.
4. **Phase 3** — delete ``services/task_executor.py`` (~1500 LOC).

Idempotent — ``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DESCRIPTION = (
    "Phase-0 cutover flag for #410 (Prefect orchestration). When 'true', "
    "TaskExecutor's _process_loop short-circuits and Prefect's deployment "
    "owns dispatch entirely — replacing the homegrown polling cadence, "
    "retry logic, and stale-task sweep with native Prefect primitives. "
    "Default 'false' = today's behavior. Flip with `UPDATE app_settings "
    "SET value = 'true' WHERE key = 'use_prefect_orchestration';` after "
    "the deployment is live and the parity gate passes. See "
    "docs/architecture/prefect-cutover.md for the staged rollout runbook."
)


async def run_migration(conn) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings
            (key, value, category, description, is_secret, is_active)
        VALUES (
            'use_prefect_orchestration',
            'false',
            'orchestration',
            $1,
            false,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        _DESCRIPTION,
    )
    logger.info(
        "20260510_182824: use_prefect_orchestration seeded 'false' — "
        "Prefect cutover Phase-0 ready, operator flips when parity confirmed"
    )
