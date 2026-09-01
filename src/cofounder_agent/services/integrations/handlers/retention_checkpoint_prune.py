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

- ``terminal_statuses`` (list[str], default :data:`_DEFAULT_TERMINAL_STATUSES`):
  task statuses that indicate a finished run whose checkpoint is safe to
  discard. This list must stay in step with the task lifecycle — a terminal
  status missing from it strands its checkpoints permanently, because nothing
  else ever revisits them. That is not hypothetical: the original list held
  only ``completed``/``published``/``failed``/``cancelled`` and omitted
  ``rejected`` + ``rejected_final``, which are the two *largest* terminal
  buckets in practice (a rejected run still executed the whole graph, so it
  leaves a full-size checkpoint). ~20k rows across the three tables had
  accumulated unreachable before this was caught. ``rejected_retry`` is
  deliberately excluded — that run is going around again and still needs its
  checkpoint.
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
from collections.abc import Mapping
from typing import Any

from services.integrations.registry import register_handler
from services.integrations.retention_backlog import BacklogQuery, register_backlog

logger = logging.getLogger(__name__)

# Tables cleared in this order. checkpoint_migrations is intentionally absent.
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

# Every pipeline_tasks status from which a run never resumes: the set
# services/pipeline_db.py stamps ``completed_at`` for -- the codebase's own
# "this run is done" definition -- plus ``completed`` and the three archival
# end-states. Deliberately absent: pending, in_progress, approved,
# awaiting_approval, awaiting_gate, rejected_retry.
_DEFAULT_TERMINAL_STATUSES = [
    "completed",
    "published",
    "failed",
    "cancelled",
    "rejected",
    "rejected_final",
    "expired",
    "dismissed",
    "dry_run",
    "superseded",
    "archived",
]
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


# Statuses meaning the run is STILL GOING. Everything else is finished and its
# checkpoint should have been reaped.
#
# This is deliberately the COMPLEMENT of terminal, not a second copy of
# ``_DEFAULT_TERMINAL_STATUSES`` — and that inversion is the whole point of the
# backlog expression (poindexter#933).
#
# Glad-Labs/poindexter#2871 was a bug IN this handler's ``terminal_statuses``
# config: it omitted ``rejected`` and ``rejected_final``, the two largest
# terminal buckets, so ~20k checkpoint rows accumulated unreachable while the
# policy reported success every run. A backlog computed from the policy's own
# predicate would have returned 0 throughout and caught nothing.
#
# Phrasing the invariant as "not active" makes a NEWLY ADDED terminal status
# count as backlog automatically, which is exactly the drift a copied terminal
# list cannot detect. Being generous here (listing a status as active when it
# is really terminal) makes the probe under-report rather than false-alarm,
# which is the correct direction for an alarm to be wrong in.
_ACTIVE_STATUSES = [
    "pending",
    "claimed",
    "in_progress",
    "awaiting_approval",
    "approved",
    "scheduled",
    # A run going around again still needs its checkpoint.
    "rejected_retry",
]


@register_backlog("checkpoint_prune")
def checkpoint_prune_backlog(row: Mapping[str, Any]) -> BacklogQuery | None:
    """Checkpoints whose source task has finished and aged out, but survive.

    Counts distinct ``checkpoints.thread_id`` values that map to a
    ``pipeline_tasks`` row which is no longer active and whose ``updated_at``
    is older than ``ttl_days``. Prefix-stripping mirrors the handler's
    ``thread_prefixes`` so ``media-``/``podcast-`` threads are matched too.

    Deliberately measured against the task lifecycle rather than this policy's
    ``terminal_statuses`` config — see :data:`_ACTIVE_STATUSES`.

    True orphans (a checkpoint whose task row no longer exists at all) are NOT
    counted here; they are #932's subject and this handler cannot reach them,
    so folding them in would report a backlog that pruning can never drain.
    """
    ttl_days = row.get("ttl_days")
    if ttl_days is None:
        return None
    ttl_days = int(ttl_days)

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    if bool(config.get("dry_run", False)):
        return None

    prefixes = config.get("thread_prefixes") or _DEFAULT_THREAD_PREFIXES
    if not isinstance(prefixes, list) or not prefixes:
        prefixes = _DEFAULT_THREAD_PREFIXES

    return BacklogQuery(
        # Built by CONCATENATION, the same way the handler builds thread_ids
        # (prefix || task_id), rather than by regex-stripping the prefix back
        # off. That keeps the two in step, avoids interpolating operator
        # config into a regex, and lets the planner hash-join: measured on the
        # live 7.1k-row table, the strip-and-match form took 9.5s and this one
        # takes 0.05s for the identical answer.
        sql="""
            SELECT COUNT(DISTINCT c.thread_id)::bigint
              FROM checkpoints c
              CROSS JOIN LATERAL unnest($2::text[]) AS p(prefix)
              JOIN pipeline_tasks pt ON c.thread_id = p.prefix || pt.task_id
             WHERE pt.status <> ALL ($3::text[])
               AND pt.updated_at < now() - make_interval(days => $1)
        """,
        params=(ttl_days, list(prefixes), list(_ACTIVE_STATUSES)),
    )
