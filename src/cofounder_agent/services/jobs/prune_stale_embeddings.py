"""PruneStaleEmbeddingsJob — TTL-based pruning of the embeddings table (#106).

Companion to :class:`CollapseOldEmbeddingsJob` (#81), which clusters and
summarizes old embeddings. This job is the simpler v1 retention layer:
for each source_table that has a configured TTL, drop rows older than
the TTL outright.

Two jobs coexist on purpose. Collapse is opt-in
(``embedding_collapse_enabled``) and biased toward preservation —
clusters get a summary row replacing them. Prune is on by default for
the high-churn sources (claude_sessions, audit) — old rows are gone,
no summary. Operators who want both can enable both: collapse runs
first (weekly), prune sweeps anything collapse left behind.

Configuration (DB-tunable, seeded by migration 0101)
----------------------------------------------------

Per-source TTLs in days, one app_settings row per source_table:

- ``embedding_retention_days.claude_sessions`` (default 21)
- ``embedding_retention_days.audit``           (default 90)
- ``embedding_retention_days.brain``           (default 365)
- ``embedding_retention_days.issues``          (default empty = no TTL)
- ``embedding_retention_days.memory``          (default empty = no TTL)
- ``embedding_retention_days.posts``           (default empty = no TTL)

An empty / unset / non-numeric value means "skip this source_table"
— the job will never delete from a source whose TTL is unconfigured.
This is the safe default: a brand-new source_table doesn't get
silently pruned because nobody set the knob.

Summary rows (``is_summary = TRUE``) are also pruned by TTL — once a
summary's age passes the limit it's by definition stale. Operators
who want forever-summaries should set the TTL high.

Schedule: nightly. Cheap query (one DELETE per source with a
time-range and source_table filter — both indexed).

Output / observability
----------------------

JobResult metrics expose:

- ``per_table``: dict of ``{source_table: rows_deleted}``
- ``ttl_days``: dict of ``{source_table: configured_days}`` (so the
  Grafana panel can chart \"how aggressive is each source\")
- ``skipped``: list of source_tables with no TTL configured
- ``total_pruned``: sum across tables — the headline gauge

Detail string is human-readable: ``\"pruned N rows across M sources\"``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from services.logger_config import get_logger

logger = get_logger(__name__)


# Settings prefix — every source_table TTL is keyed
# ``embedding_retention_days.<source_table>``.
_PREFIX = "embedding_retention_days."


def _parse_days(raw: Any) -> int | None:
    """Parse a TTL value. Returns None for "no TTL" (empty / non-numeric).

    The empty-string case is intentional — it lets a migration write a
    row with an explicit "do not prune" semantic, distinct from "key
    isn't in app_settings yet" (also returns None). Both paths skip
    the source table; the difference only shows up in audits.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        n = int(text)
    except (ValueError, TypeError):
        return None
    if n <= 0:
        return None
    return n


async def _load_ttls(pool: Any) -> dict[str, int | None]:
    """Read every ``embedding_retention_days.*`` row from app_settings.

    Returns a dict keyed by source_table → days (int) or None for
    "skip." Reads the full prefix in one query; cheap.
    """
    ttls: dict[str, int | None] = {}
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
    for row in rows:
        source_table = row["key"][len(_PREFIX):]
        if not source_table:
            continue
        ttls[source_table] = _parse_days(row["value"])
    return ttls


class PruneStaleEmbeddingsJob:
    """Delete embeddings older than each source_table's configured TTL."""

    name = "prune_stale_embeddings"
    description = (
        "Drop embeddings rows whose created_at is older than the "
        "configured TTL for their source_table. Per-source TTLs live "
        "in app_settings under the embedding_retention_days.* prefix."
    )
    # Daily at 03:17 UTC — outside the Discord posting window and the
    # peak compute window. Apscheduler accepts standard cron syntax.
    schedule = "17 3 * * *"
    # Two overlapping runs would just both delete the same rows; the
    # second sees an empty candidate set and exits in milliseconds.
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # Plugin config can override the prefix for testing — production
        # always uses the default.
        prefix_override = (config or {}).get("settings_prefix")
        ttls = await _load_ttls_with_prefix(pool, prefix_override)

        per_table: dict[str, int] = {}
        skipped: list[str] = []
        ttl_days: dict[str, int] = {}

        for source_table, days in ttls.items():
            if days is None:
                skipped.append(source_table)
                continue
            ttl_days[source_table] = days
            try:
                deleted = await _prune_one_source(
                    pool, source_table=source_table, days=days,
                )
            except Exception as exc:  # noqa: BLE001 — never crash the scheduler
                logger.exception(
                    "[prune_stale_embeddings] source=%s failed: %s",
                    source_table, exc,
                )
                # Log + skip rather than fail the whole job — one bad
                # source shouldn't stop the others from being pruned.
                continue
            per_table[source_table] = deleted

        total_pruned = sum(per_table.values())

        if not per_table and not skipped:
            return JobResult(
                ok=True,
                detail="no embedding_retention_days.* settings found — nothing to prune",
                changes_made=0,
                metrics={"per_table": {}, "skipped": [], "ttl_days": {}, "total_pruned": 0},
            )

        detail = (
            f"pruned {total_pruned} rows across {len(per_table)} source(s); "
            f"{len(skipped)} source(s) had no TTL"
        )
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=total_pruned,
            metrics={
                "per_table": per_table,
                "skipped": skipped,
                "ttl_days": ttl_days,
                "total_pruned": total_pruned,
            },
        )


async def _load_ttls_with_prefix(
    pool: Any, prefix_override: str | None,
) -> dict[str, int | None]:
    """Same as :func:`_load_ttls` but with an injectable prefix for tests."""
    if prefix_override is None:
        return await _load_ttls(pool)
    ttls: dict[str, int | None] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT key, value
              FROM app_settings
             WHERE key LIKE $1
               AND COALESCE(is_active, TRUE) = TRUE
            """,
            f"{prefix_override}%",
        )
    for row in rows:
        source_table = row["key"][len(prefix_override):]
        if not source_table:
            continue
        ttls[source_table] = _parse_days(row["value"])
    return ttls


async def _prune_one_source(
    pool: Any,
    *,
    source_table: str,
    days: int,
) -> int:
    """Delete embeddings older than ``days`` for one source_table.

    Returns the number of rows deleted. Uses the existing
    ``(source_table, created_at)`` access pattern — embeddings has a
    unique index on ``(source_table, source_id, chunk_index, embedding_model)``
    plus heavy churn is concentrated on a handful of source_tables, so
    even without a dedicated ``(source_table, created_at)`` index the
    delete is bounded by source-table partition.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings
             WHERE source_table = $1
               AND created_at < $2
            """,
            source_table, cutoff,
        )
    # asyncpg returns "DELETE N".
    try:
        deleted = int(result.split()[-1])
    except (ValueError, IndexError):
        deleted = 0
    if deleted:
        logger.info(
            "[prune_stale_embeddings] source=%s ttl=%dd cutoff=%s pruned=%d",
            source_table, days, cutoff.isoformat(), deleted,
        )
    return deleted


__all__ = ["PruneStaleEmbeddingsJob"]
