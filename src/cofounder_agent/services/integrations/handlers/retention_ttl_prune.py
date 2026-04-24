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
from typing import Any

from services.integrations.registry import register_handler

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
            count_sql = f"SELECT COUNT(*)::bigint FROM {table_name} WHERE {where_clause}"
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
            """
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
