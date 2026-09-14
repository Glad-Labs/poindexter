"""Handler: ``retention.ttl_prune``.

Generic TTL-based pruner. Reads ``row.table_name``, ``row.age_column``,
``row.ttl_days``, and optionally ``row.filter_sql`` to build a DELETE
statement and execute it. Returns the number of rows deleted.

The row's ``config`` JSONB can carry:
- ``batch_size`` (int, default 10000) — limit the single-pass delete.
  Values well above this just loop until the remaining set is empty.
- ``dry_run`` (bool, default false) — count without deleting. Useful
  for a first-pass "what would this remove?" check before flipping
  enabled=true.

The handler constructs SQL from row fields via string interpolation.
Those fields come from operator-controlled migration seeds, not user
input, so string interpolation is acceptable here — but we validate
identifiers (``table_name``, ``age_column``) against a conservative
whitelist so a typo can't turn into a SQL injection via a malicious
seed migration.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from poindexter.services.integrations.registry import register_handler
from poindexter.services.integrations.retention_backlog import BacklogQuery, register_backlog

logger = logging.getLogger(__name__)


# Conservative identifier whitelist: letters, digits, underscore, dot
# (for schema.table references). Rejects anything with quotes,
# semicolons, whitespace, etc.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _validate_identifier(value: str, field_name: str) -> str:
    if not value or not _IDENT_RE.match(value):
        raise ValueError(
            f"retention.ttl_prune: invalid {field_name}={value!r} — "
            f"must match {_IDENT_RE.pattern}"
        )
    return value


@register_handler("retention", "ttl_prune")
async def ttl_prune(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Delete rows older than ``row.ttl_days`` from ``row.table_name``."""
    if pool is None:
        raise RuntimeError("retention.ttl_prune: pool unavailable")

    ttl_days = row.get("ttl_days")
    if ttl_days is None:
        raise ValueError("retention.ttl_prune: ttl_days is required")
    try:
        ttl_days = int(ttl_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"retention.ttl_prune: ttl_days must be int, got {ttl_days!r}"
        ) from exc
    if ttl_days < 0:
        raise ValueError(f"retention.ttl_prune: ttl_days must be >= 0, got {ttl_days}")

    table_name = _validate_identifier(row.get("table_name") or "", "table_name")
    age_column = _validate_identifier(row.get("age_column") or "created_at", "age_column")

    filter_sql = row.get("filter_sql") or ""
    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    batch_size = int(config.get("batch_size") or 10000)
    dry_run = bool(config.get("dry_run", False))

    where_parts = [f"{age_column} < now() - make_interval(days => $1)"]
    if filter_sql.strip():
        where_parts.append(f"({filter_sql})")
    where_clause = " AND ".join(where_parts)

    async with pool.acquire() as conn:
        if dry_run:
            count_sql = f"SELECT COUNT(*)::bigint FROM {table_name} WHERE {where_clause}"  # nosec B608  # table_name+age_column validated by _validate_identifier; filter_sql is operator-controlled migration seed (see module docstring)
            would_delete = await conn.fetchval(count_sql, ttl_days)
            logger.info(
                "[retention.ttl_prune] %s: DRY RUN — would delete %s rows older than %s days",
                row.get("name"), would_delete, ttl_days,
            )
            return {
                "dry_run": True,
                "would_delete": int(would_delete or 0),
                "deleted": 0,
            }

        # Batched delete so giant tables don't take an exclusive lock
        # for minutes. Each batch is its own autocommitted statement
        # and the loop exits when an iteration deletes 0 rows.
        total_deleted = 0
        while True:
            delete_sql = f"""
                DELETE FROM {table_name}
                 WHERE ctid IN (
                     SELECT ctid FROM {table_name}
                      WHERE {where_clause}
                      LIMIT $2
                 )
            """  # nosec B608  # table_name+age_column validated by _validate_identifier; filter_sql is operator-controlled migration seed (see module docstring)
            result = await conn.execute(delete_sql, ttl_days, batch_size)
            # asyncpg returns "DELETE <count>"
            try:
                batch_deleted = int(result.rsplit(" ", 1)[-1])
            except (ValueError, IndexError):
                batch_deleted = 0
            total_deleted += batch_deleted
            logger.debug(
                "[retention.ttl_prune] %s: batch deleted=%d total=%d",
                row.get("name"), batch_deleted, total_deleted,
            )
            if batch_deleted < batch_size:
                break

    logger.info(
        "[retention.ttl_prune] %s: deleted %d rows from %s older than %s days",
        row.get("name"), total_deleted, table_name, ttl_days,
    )
    return {"deleted": total_deleted, "table": table_name, "ttl_days": ttl_days}


def _run_anchor(value: Any) -> datetime | None:
    """``last_run_at`` as a tz-aware datetime, or None when the row has none.

    Rows straight from asyncpg carry a datetime; rows that travelled through
    JSON carry an ISO string, and asyncpg refuses a str for a timestamptz bind.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@register_backlog("ttl_prune")
def ttl_prune_backlog(row: Mapping[str, Any]) -> BacklogQuery | None:
    """Rows this policy's own predicate still matches (poindexter#933).

    For a TTL pruner the policy row IS the invariant — "rows older than
    ``ttl_days`` matching ``filter_sql`` should be gone" — so counting what its
    own WHERE clause matches is exactly the right question. A correct policy
    drains this to ~0 each run; a broken one accumulates.

    The count is anchored at the policy's own ``last_run_at`` when the row
    carries one: only rows that were ALREADY past the TTL when the pruner last
    ran are counted, so inflow since that pass never shows up as backlog. A
    correct policy therefore reads ~0 at any moment after its run; a broken one
    reads the rows it left behind. A row fetched without ``last_run_at`` falls
    back to ``now()``, which measures residue plus inflow.

    Earned 2026-09-13: ``live_activity`` (2-day TTL, ~9 rows/min of inflow)
    drained completely every run — ``last_run_deleted`` in the thousands,
    ``last_error`` NULL — yet read 141 "overdue" rows a quarter-hour after each
    pass and paged ``retention_backlog`` four times a day, because the probe's
    persistence rule cannot tell steady inflow from a steady residue. Persistence
    across consecutive probes remains the caller's signal (see
    ``services/jobs/probe_retention_backlog.py``); this anchor makes the number
    it persists over mean what the finding says.

    A ``dry_run`` policy deletes nothing by design, so its backlog is
    meaningless as a fault signal and it declares none rather than alarming
    forever.
    """
    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    if bool(config.get("dry_run", False)):
        return None

    ttl_days = row.get("ttl_days")
    if ttl_days is None:
        return None
    ttl_days = int(ttl_days)

    # Same validation the handler applies — a backlog query is still SQL built
    # from row fields, so it gets the identical identifier whitelist.
    table_name = _validate_identifier(row.get("table_name") or "", "table_name")
    age_column = _validate_identifier(row.get("age_column") or "created_at", "age_column")

    anchor = _run_anchor(row.get("last_run_at"))
    params: tuple[Any, ...]
    if anchor is not None:
        where_parts = [f"{age_column} < $2::timestamptz - make_interval(days => $1)"]
        params = (ttl_days, anchor)
    else:
        where_parts = [f"{age_column} < now() - make_interval(days => $1)"]
        params = (ttl_days,)
    filter_sql = row.get("filter_sql") or ""
    if filter_sql.strip():
        where_parts.append(f"({filter_sql})")
    where_clause = " AND ".join(where_parts)

    return BacklogQuery(
        sql=f"SELECT COUNT(*)::bigint FROM {table_name} WHERE {where_clause}",  # nosec B608  # identifiers validated above; filter_sql is an operator-controlled migration seed (see module docstring)
        params=params,
    )
