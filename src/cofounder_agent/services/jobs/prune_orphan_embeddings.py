"""PruneOrphanEmbeddingsJob — drop embeddings whose source row is gone (#106).

Companion to :class:`PruneStaleEmbeddingsJob`. That one prunes by age;
this one prunes by reference: if an embedding's
``(source_table, source_id)`` no longer points to a real row, the
embedding is orphaned and safe to delete.

Why a separate job
------------------

Different cadence and failure modes. Orphan cleanup needs a precise
join against each source's *real* table, so it's source-specific in a
way TTL-pruning isn't. Splitting keeps both jobs small and
independently testable. The TTL job will catch most orphans
incidentally over time; this job exists for the cases where source
rows churn faster than the TTL (issues marked closed and reaped, post
drafts cancelled).

Safety model — opt-in per source
--------------------------------

Default behavior: **the job does nothing**. To enable orphan cleanup
for a source_table, the operator flips one app_setting:

    poindexter settings set embedding_orphan_check.posts on
    poindexter settings set embedding_orphan_check.audit on

No auto-defaults. The reason is the join semantics are source-specific
and a wrong query would silently delete healthy embeddings:

- ``posts``  source_id = UUID; joins ``posts.id``.
- ``audit``  source_id = audit_log.id (integer-as-text); joins ``audit_log.id``.
- ``brain``  source_id = compound ``"<table>/<id>"`` (e.g.
  ``"brain_decisions/5105"``) — parsed before joining the right base table.
- ``issues`` source_id = integer-as-text; joins ``gitea_issues.number``
  if the local cache table exists.
- ``claude_sessions`` and ``memory``: source rows live outside the DB
  (Claude Code transcripts, memory files), so this job intentionally
  has no handler — TTL pruning is the only mechanism.
- ``samples``: synthetic test data; no handler.

Each handler is a small, audited SQL fragment. Adding a new source
means writing a handler in this file (under ``_HANDLERS``) and the
operator turning it on. No automatic registration.

Configuration (DB-tunable)
--------------------------

- ``embedding_orphan_check.<source_table>``  per-source enable flag
  (``on`` / ``off``, default off). Reads via
  :func:`approval_service.is_gate_enabled`-style truthy parsing.
- ``embedding_orphan_check_batch_size``  cap per-run deletes per
  source (default 1000) so a runaway source can't lock the table for
  minutes.

Schedule: nightly at 03:23 UTC (paired with the TTL job at 03:17 so
they don't collide on the same source's rows).

Output / observability
----------------------

JobResult metrics:

- ``per_table``: ``{source_table: rows_deleted}``
- ``checked_table``: list of sources actually processed
- ``skipped``: list of sources whose enable flag is off (or not
  in the handler registry at all)
- ``total_orphans_pruned``: headline gauge for Grafana.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from plugins.job import JobResult
from services.logger_config import get_logger

logger = get_logger(__name__)


# Settings prefix for per-source enable flags.
_PREFIX = "embedding_orphan_check."

# Default cap per source per run — protects against an accidental
# wholesale deletion if a source table got truncated.
_DEFAULT_BATCH_SIZE = 1000


def _truthy(value: Any) -> bool:
    """Same truthy semantics every other gate check uses."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("on", "true", "1", "yes")


def _parse_int(raw: Any, default: int) -> int:
    if raw is None:
        return default
    try:
        n = int(str(raw).strip())
    except (ValueError, TypeError):
        return default
    return n if n > 0 else default


# ---------------------------------------------------------------------------
# Per-source orphan handlers
# ---------------------------------------------------------------------------
#
# Each handler runs a single DELETE that removes embeddings whose
# (source_table, source_id) no longer corresponds to a real row in the
# underlying table. Handlers are async so they can use the connection
# directly and so they can be patched in tests.
#
# Returns: int rows deleted.
# ---------------------------------------------------------------------------


async def _orphan_posts(pool: Any, batch_size: int) -> int:
    """Posts: source_id is UUID, joins posts.id."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'posts'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN posts p ON p.id::text = e2.source_id
                    WHERE e2.source_table = 'posts'
                      AND p.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


async def _orphan_audit(pool: Any, batch_size: int) -> int:
    """Audit: source_id = audit_log.id (integer as text)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'audit'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN audit_log a ON a.id::text = e2.source_id
                    WHERE e2.source_table = 'audit'
                      AND a.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


async def _orphan_brain(pool: Any, batch_size: int) -> int:
    """Brain: source_id is compound ``"<table>/<id>"``.

    Today the only prefix in use is ``brain_decisions/<id>``. The
    handler parses that and joins on ``brain_decisions.id``. Other
    prefixes are skipped (left for a future migration if/when they
    appear) so we never delete an embedding the join can't safely
    resolve.
    """
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings e
             WHERE e.source_table = 'brain'
               AND e.id IN (
                   SELECT e2.id
                     FROM embeddings e2
                LEFT JOIN brain_decisions b
                       ON b.id::text = split_part(e2.source_id, '/', 2)
                    WHERE e2.source_table = 'brain'
                      AND e2.source_id LIKE 'brain_decisions/%'
                      AND b.id IS NULL
                    LIMIT $1
               )
            """,
            batch_size,
        )
    return _deleted_count(result)


_HANDLERS: dict[str, Callable[[Any, int], Awaitable[int]]] = {
    "posts": _orphan_posts,
    "audit": _orphan_audit,
    "brain": _orphan_brain,
}


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------


async def _load_enabled_sources(pool: Any) -> dict[str, bool]:
    """Read every ``embedding_orphan_check.*`` row and parse its truthy."""
    out: dict[str, bool] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT key, value
              FROM app_settings
             WHERE key LIKE $1
               AND COALESCE(is_active, TRUE) = TRUE
            """,
            f"{_PREFIX}%",
        )
        batch = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            "embedding_orphan_check_batch_size",
        )
    for row in rows:
        source_table = row["key"][len(_PREFIX):]
        if not source_table:
            continue
        out[source_table] = _truthy(row["value"])
    out["__batch_size__"] = _parse_int(batch, _DEFAULT_BATCH_SIZE)  # type: ignore[assignment]
    return out


def _deleted_count(execute_result: str) -> int:
    """asyncpg returns 'DELETE N' — extract N."""
    try:
        return int(execute_result.split()[-1])
    except (ValueError, IndexError):
        return 0


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


class PruneOrphanEmbeddingsJob:
    """Delete embeddings whose source row no longer exists, per opt-in source."""

    name = "prune_orphan_embeddings"
    description = (
        "Drop embeddings rows whose (source_table, source_id) no longer "
        "matches a row in the underlying source table. Opt-in per source "
        "via app_settings.embedding_orphan_check.<source>=on."
    )
    schedule = "23 3 * * *"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        enabled = await _load_enabled_sources(pool)
        batch_size: int = enabled.pop("__batch_size__", _DEFAULT_BATCH_SIZE)  # type: ignore[arg-type]

        per_table: dict[str, int] = {}
        skipped: list[str] = []
        checked: list[str] = []

        for source_table, handler in _HANDLERS.items():
            if not enabled.get(source_table, False):
                skipped.append(source_table)
                continue
            try:
                deleted = await handler(pool, batch_size)
            except Exception as exc:  # noqa: BLE001 — never crash the scheduler
                logger.exception(
                    "[prune_orphan_embeddings] source=%s handler failed: %s",
                    source_table, exc,
                )
                continue
            checked.append(source_table)
            per_table[source_table] = deleted
            if deleted:
                logger.info(
                    "[prune_orphan_embeddings] source=%s pruned %d orphan(s)",
                    source_table, deleted,
                )

        # Sources the operator enabled that we don't have a handler for
        # — surface in metrics so they can see why nothing happened.
        unknown_enabled = [
            s for s, on in enabled.items()
            if on and s not in _HANDLERS
        ]

        total = sum(per_table.values())
        if not checked and not unknown_enabled:
            return JobResult(
                ok=True,
                detail=(
                    "no orphan-check sources enabled — set "
                    "embedding_orphan_check.<source>=on to opt in"
                ),
                changes_made=0,
                metrics={
                    "per_table": {},
                    "skipped": skipped,
                    "checked": [],
                    "total_orphans_pruned": 0,
                    "unknown_enabled": unknown_enabled,
                    "batch_size": batch_size,
                },
            )

        detail = (
            f"orphan-pruned {total} rows across {len(checked)} source(s)"
        )
        if unknown_enabled:
            detail += (
                f"; {len(unknown_enabled)} enabled source(s) had no handler"
            )

        return JobResult(
            ok=True,
            detail=detail,
            changes_made=total,
            metrics={
                "per_table": per_table,
                "skipped": skipped,
                "checked": checked,
                "total_orphans_pruned": total,
                "unknown_enabled": unknown_enabled,
                "batch_size": batch_size,
            },
        )


__all__ = [
    "PruneOrphanEmbeddingsJob",
    "_HANDLERS",
    "_DEFAULT_BATCH_SIZE",
]
