"""Refresh app_settings descriptions made stale by the Prefect Stage 4
deletion of ``services/task_executor.py`` (2026-05-16).

Two earlier migrations seeded descriptions referencing ``TaskExecutor``
as a live component:

- ``20260510_182824_seed_prefect_cutover_flag.py`` — seeded
  ``use_prefect_orchestration`` with text describing TaskExecutor's
  short-circuit behaviour.
- ``0000_baseline.seeds.sql`` — seeded ``worker_heartbeat_interval_seconds``
  with text describing TaskExecutor stamping ``content_tasks.updated_at``.

Both code paths are gone: TaskExecutor was deleted entirely, Prefect's
``content_generation_flow`` is the sole dispatcher, and the table was
renamed ``content_tasks`` → ``pipeline_tasks`` long before. Operators
inspecting these settings via ``poindexter settings show`` (or the
auto-generated ``docs/reference/app-settings.md``) would otherwise see
descriptions referencing modules that no longer exist — exactly the
kind of stale signal the codebase-currency feedback calls out.

Idempotent — uses ``UPDATE`` keyed on the immutable ``key`` column.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_PREFECT_FLAG_DESCRIPTION = (
    "Prefect orchestration cutover flag (#410 Stage 4 complete "
    "2026-05-16). Permanently true — services/task_executor.py was "
    "deleted; Prefect's content_generation_flow is the sole dispatch "
    "path. Setting this to 'false' has no effect; the row is kept "
    "so historical migrations remain idempotent. See "
    "docs/architecture/prefect-cutover.md for the full cutover history."
)

_HEARTBEAT_DESCRIPTION = (
    "Worker heartbeat cadence. While processing a single task the "
    "Prefect content_generation_flow stamps "
    "pipeline_tasks.updated_at = NOW() every N seconds so the "
    "stale-task sweep can tell a live worker apart from a crashed "
    "one. Lower = faster failure detection; higher = less DB chatter. "
    "Must stay well below ``stale_task_timeout_minutes * 60``."
)


async def run_migration(conn) -> None:
    await conn.execute(
        "UPDATE app_settings SET description = $1 "
        "WHERE key = 'use_prefect_orchestration'",
        _PREFECT_FLAG_DESCRIPTION,
    )
    await conn.execute(
        "UPDATE app_settings SET description = $1 "
        "WHERE key = 'worker_heartbeat_interval_seconds'",
        _HEARTBEAT_DESCRIPTION,
    )
    logger.info(
        "20260516_201048: refreshed app_settings descriptions for "
        "use_prefect_orchestration + worker_heartbeat_interval_seconds "
        "(removed stale TaskExecutor references after Stage 4 deletion)"
    )
