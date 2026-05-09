"""Handler: ``retention.downsample``.

Keeps recent raw data, aggregates older data into a coarser rollup
table, then deletes the raw rows the rollup replaces. The classic
"store 1-minute GPU metrics for 30 days, hourly aggregates for 365".

Configuration lives in ``row.downsample_rule`` (jsonb):

.. code:: json

    {
      "keep_raw_days": 30,
      "rollup_table": "gpu_metrics_hourly",
      "rollup_interval": "1 hour",
      "aggregations": [
        {"col": "utilization_pct", "fn": "avg", "as": "avg_utilization"},
        {"col": "power_watts",     "fn": "max", "as": "peak_watts"}
      ],
      "group_by": []
    }

The handler:

1. Inserts aggregated rows into ``rollup_table`` for buckets older
   than ``keep_raw_days`` that don't already have a rollup row.
2. Deletes raw rows older than ``keep_raw_days``.

The rollup table schema must be created separately by an explicit
migration — this handler doesn't auto-create. That's intentional:
every rollup schema decision (indexes, constraints, column types)
is a deliberate choice the operator should review.

For v1, ``group_by`` is fixed to ``[]`` (no extra grouping beyond the
time bucket) and ``aggregations`` accepts ``avg``, ``min``, ``max``,
``sum``, ``count`` — same as the tests cover. Additional aggregation
functions require whitelist extension below.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
_ALLOWED_FNS = {"avg", "min", "max", "sum", "count"}
_INTERVAL_RE = re.compile(
    r"^\d+\s+(second|minute|hour|day|week|month|year)s?$", re.IGNORECASE
)


def _validate_identifier(value: str, field_name: str) -> str:
    if not value or not _IDENT_RE.match(value):
        raise ValueError(
            f"retention.downsample: invalid {field_name}={value!r}"
        )
    return value


def _validate_interval(value: str) -> str:
    if not value or not _INTERVAL_RE.match(value):
        raise ValueError(
            f"retention.downsample: invalid rollup_interval={value!r} "
            "— expected e.g. '1 hour', '30 seconds', '7 days'"
        )
    return value


def _build_aggregations(rule_aggs: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(select_exprs, insert_cols)`` SQL fragments.

    ``select_exprs`` is the SELECT-list form (``fn(col) AS alias, ...``)
    and ``insert_cols`` is the matching INSERT column-list form
    (``alias, ...``). Built together so the two stay in sync.
    """
    select_parts: list[str] = []
    insert_parts: list[str] = []
    for agg in rule_aggs:
        col = _validate_identifier(agg.get("col", ""), "aggregation.col")
        fn = (agg.get("fn") or "").lower()
        if fn not in _ALLOWED_FNS:
            raise ValueError(
                f"retention.downsample: aggregation fn={fn!r} "
                f"not in {sorted(_ALLOWED_FNS)}"
            )
        alias = agg.get("as") or f"{fn}_{col}"
        alias = _validate_identifier(alias, "aggregation.as")
        select_parts.append(f"{fn}({col}) AS {alias}")
        insert_parts.append(alias)
    if not select_parts:
        raise ValueError("retention.downsample: aggregations list is empty")
    return ", ".join(select_parts), ", ".join(insert_parts)


@register_handler("retention", "downsample")
async def downsample(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Aggregate old rows into ``rollup_table``, delete the raw ones."""
    if pool is None:
        raise RuntimeError("retention.downsample: pool unavailable")

    rule = row.get("downsample_rule") or {}
    if not isinstance(rule, dict) or not rule:
        raise ValueError("retention.downsample: downsample_rule is required")

    keep_raw_days = rule.get("keep_raw_days")
    if keep_raw_days is None:
        raise ValueError("retention.downsample: keep_raw_days is required")
    keep_raw_days = int(keep_raw_days)

    table_name = _validate_identifier(row.get("table_name") or "", "table_name")
    age_column = _validate_identifier(row.get("age_column") or "created_at", "age_column")
    rollup_table = _validate_identifier(rule.get("rollup_table") or "", "rollup_table")
    interval = _validate_interval(rule.get("rollup_interval") or "")
    select_exprs, insert_cols = _build_aggregations(rule.get("aggregations") or [])

    config = row.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    dry_run = bool(config.get("dry_run", False))

    async with pool.acquire() as conn:
        # (1) How many raw rows would we affect?
        count_sql = (
            f"SELECT COUNT(*)::bigint FROM {table_name} "  # nosec B608  # table_name validated by _validate_identifier (regex whitelist)
            f"WHERE {age_column} < now() - make_interval(days => $1)"
        )
        raw_count = int(await conn.fetchval(count_sql, keep_raw_days) or 0)

        if raw_count == 0:
            logger.info(
                "[retention.downsample] %s: nothing older than %d days",
                row.get("name"), keep_raw_days,
            )
            return {"rolled_up": 0, "deleted": 0, "bucket_interval": interval}

        if dry_run:
            logger.info(
                "[retention.downsample] %s: DRY RUN — %d raw rows would be "
                "bucketed into %s at %s and then deleted",
                row.get("name"), raw_count, rollup_table, interval,
            )
            return {
                "dry_run": True,
                "would_affect": raw_count,
                "bucket_interval": interval,
                "rolled_up": 0,
                "deleted": 0,
            }

        # (2) Insert rollup buckets that don't yet exist in the rollup
        # table. We rely on the rollup_table having a unique constraint
        # on bucket_start (or a suitable PK) so ON CONFLICT prevents
        # double-inserting the same bucket if the job overlaps itself.
        insert_sql = f"""
            INSERT INTO {rollup_table}
                (bucket_start, {insert_cols})
            SELECT
                date_trunc('hour', {age_column}) AS bucket_start,
                {select_exprs}
              FROM {table_name}
             WHERE {age_column} < now() - make_interval(days => $1)
             GROUP BY 1
             ON CONFLICT (bucket_start) DO NOTHING
        """  # nosec B608  # rollup_table/age_column/table_name/insert_cols validated by _validate_identifier; select_exprs built by _build_aggregations against _ALLOWED_FNS whitelist
        insert_result = await conn.execute(insert_sql, keep_raw_days)
        try:
            rolled_up = int(insert_result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            rolled_up = 0

        # (3) Delete the raw rows we just aggregated.
        delete_sql = (
            f"DELETE FROM {table_name} "  # nosec B608  # table_name validated by _validate_identifier (regex whitelist)
            f"WHERE {age_column} < now() - make_interval(days => $1)"
        )
        del_result = await conn.execute(delete_sql, keep_raw_days)
        try:
            deleted = int(del_result.rsplit(" ", 1)[-1])
        except (ValueError, IndexError):
            deleted = 0

    logger.info(
        "[retention.downsample] %s: rolled %d buckets into %s, deleted %d raw rows",
        row.get("name"), rolled_up, rollup_table, deleted,
    )
    return {
        "rolled_up": rolled_up,
        "deleted": deleted,
        "bucket_interval": interval,
    }
