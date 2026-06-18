"""Handler: ``retention.checkpoint_prune``.

Deletes LangGraph Postgres-checkpointer rows (``checkpoints``,
``checkpoint_blobs``, ``checkpoint_writes``) for pipeline runs that have
reached a terminal status and whose ``pipeline_tasks.updated_at`` is older
than ``row.ttl_days`` days.

Unlike the other retention handlers, the checkpoint tables carry no
timestamp column of their own — cleanup is driven by the terminal status
and age of the **source task row** rather than by time on the checkpoint
row itself. The ``ttl_days`` on the policy row sets the age threshold for
``pipeline_tasks.updated_at``.

The content pipeline sets ``thread_id == task_id`` for ``canonical_blog``
checkpoints (``template_runner.TemplateRunner.run``). The media and podcast
pipelines prefix with ``media-`` and ``podcast-`` respectively
(``dispatch_media_pipeline.py`` / ``dispatch_podcast_pipeline.py``). All
three variants are swept when their source task reaches a terminal status.

Checkpoint cleanup for completed runs also mitigates the checkpoint-
poisoning failure mode where a killed run's leftover checkpoint
short-circuits retries — see the note in
``services/tasks_db._clear_checkpoints_for_threads`` and
``docs/references/langgraph_checkpoint_poisoning.md``. The stale-task
sweeper handles the mid-run case; this handler handles the *completed-run*
accumulation case.

## Config (``row.config`` JSONB)

- ``terminal_statuses`` (list[str], default ``["completed", "published",
  "failed", "cancelled"]``): task statuses that indicate a finished run
  whose checkpoint is safe to discard.
- ``thread_prefixes`` (list[str], default ``["", "media-", "podcast-"]``):
  prefixes prepended to each ``task_id`` when building ``thread_id`` values
  to delete. Adjust if future pipeline variants add additional prefixes.
- ``batch_size`` (int, default 1000): max tasks processed per run to keep
  the DELETE statement size predictable on large installs.
- ``dry_run`` (bool, default false): count rows without deleting.

## Tables touched (in order)

``checkpoint_writes`` → ``checkpoint_blobs`` → ``checkpoints``.
``checkpoint_migrations`` is intentionally skipped (no ``thread_id``
column; it holds LangGraph schema-version state, not per-run data).

If a checkpoint table doesn't exist (Postgres checkpointer never enabled
on this install) the handler skips it silently — same guard used by
``tasks_db._clear_checkpoints_for_threads``.
"""

from __future__ import annotations

import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)

# Tables cleared in this order. checkpoint_migrations is intentionally absent.
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

_DEFAULT_TERMINAL_STATUSES = ["completed", "published", "failed", "cancelled"]
_DEFAULT_THREAD_PREFIXES = ["", "media-", "podcast-"]
_DEFAULT_BATCH_SIZE = 1000


@register_handler("retention", "checkpoint_prune")
async def checkpoint_prune(
    payload: Any,  # noqa: ARG001 — unused; required by retention handler protocol
    *,
    site_config: Any,  # noqa: ARG002 — unused; required by retention handler protocol
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Delete LangGraph checkpoint rows for terminal-status pipeline tasks."""
    if pool is None:
        raise RuntimeError("retention.checkpoint_prune: pool unavailable")

    ttl_days = row.get("ttl_days")
    if ttl_days is None:
        raise ValueError("retention.checkpoint_prune: ttl_days is required")
    try:
        ttl_days = int(ttl_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"retention.checkpoint_prune: ttl_days must be int, got {ttl_days!r}"
        ) from exc
    if ttl_days < 0:
        raise ValueError(
            f"retention.checkpoint_prune: ttl_days must be >= 0, got {ttl_days}"
        )

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    terminal_statuses: list[str] = list(
        config.get("terminal_statuses") or _DEFAULT_TERMINAL_STATUSES
    )
    thread_prefixes: list[str] = list(
        config.get("thread_prefixes") or _DEFAULT_THREAD_PREFIXES
    )
    batch_size = int(config.get("batch_size") or _DEFAULT_BATCH_SIZE)
    dry_run = bool(config.get("dry_run", False))

    async with pool.acquire() as conn:
        # Guard: skip entirely when checkpoints table doesn't exist — the
        # Postgres checkpointer was never enabled on this install.
        cp_exists = await conn.fetchval(
            "SELECT to_regclass('public.checkpoints') IS NOT NULL"
        )
        if not cp_exists:
            logger.info(
                "[retention.checkpoint_prune] %s: checkpoints table absent — skip",
                row.get("name"),
            )
            return {"deleted": 0, "skipped": "checkpoints table not present"}

        # Find terminal-status task_ids whose updated_at is older than ttl_days.
        task_rows = await conn.fetch(
            """
            SELECT task_id FROM pipeline_tasks
             WHERE status = ANY($1::text[])
               AND updated_at < now() - make_interval(days => $2)
             LIMIT $3
            """,
            terminal_statuses,
            ttl_days,
            batch_size,
        )

        if not task_rows:
            logger.debug(
                "[retention.checkpoint_prune] %s: no terminal tasks older than %dd",
                row.get("name"), ttl_days,
            )
            return {"deleted": 0, "tasks_processed": 0}

        # Build thread_id set including pipeline-variant prefixes.
        thread_ids: list[str] = []
        for r in task_rows:
            tid = r["task_id"]
            for prefix in thread_prefixes:
                thread_ids.append(f"{prefix}{tid}")

        if dry_run:
            would_delete = 0
            for table in _CHECKPOINT_TABLES:
                t_exists = await conn.fetchval(
                    "SELECT to_regclass($1::text) IS NOT NULL", table
                )
                if not t_exists:
                    continue
                n = await conn.fetchval(
                    f"SELECT COUNT(*)::bigint FROM {table} WHERE thread_id = ANY($1::text[])",  # nosec B608  # table is a compile-time constant from _CHECKPOINT_TABLES
                    thread_ids,
                )
                would_delete += int(n or 0)
            logger.info(
                "[retention.checkpoint_prune] %s: DRY RUN — %d tasks, "
                "would delete ~%d checkpoint rows (ttl=%sd)",
                row.get("name"), len(task_rows), would_delete, ttl_days,
            )
            return {
                "dry_run": True,
                "tasks": len(task_rows),
                "would_delete": would_delete,
                "deleted": 0,
            }

        # Delete checkpoint rows across all three tables.
        total_deleted = 0
        for table in _CHECKPOINT_TABLES:
            t_exists = await conn.fetchval(
                "SELECT to_regclass($1::text) IS NOT NULL", table
            )
            if not t_exists:
                continue
            result = await conn.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY($1::text[])",  # nosec B608  # table is a compile-time constant from _CHECKPOINT_TABLES
                thread_ids,
            )
            try:
                batch_deleted = int(str(result).rsplit(" ", 1)[-1])
            except (ValueError, IndexError):
                batch_deleted = 0
            total_deleted += batch_deleted
            logger.debug(
                "[retention.checkpoint_prune] %s: %s deleted=%d",
                row.get("name"), table, batch_deleted,
            )

    logger.info(
        "[retention.checkpoint_prune] %s: deleted %d checkpoint rows "
        "for %d terminal tasks (ttl=%sd)",
        row.get("name"), total_deleted, len(task_rows), ttl_days,
    )
    return {
        "deleted": total_deleted,
        "tasks_processed": len(task_rows),
        "thread_ids_checked": len(thread_ids),
    }
