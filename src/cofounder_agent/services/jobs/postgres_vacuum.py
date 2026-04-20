"""PostgresVacuumJob — keep high-churn tables from bloating with dead tuples.

Autovacuum should handle this, but we've observed slow queries and index
bloat on the tables where the pipeline churns hardest (``embeddings``
growing by thousands of rows per tap cycle, ``audit_log`` and
``cost_logs`` appending constantly, ``content_tasks`` + ``pipeline_tasks``
cycling through status transitions).

This job runs ``VACUUM (ANALYZE)`` on a configured list of tables every
6 hours by default. ``ANALYZE`` rebuilds planner statistics, which is
the other thing that degrades when autovacuum falls behind.

## Why not VACUUM FULL

``VACUUM FULL`` rewrites the table and reclaims disk space but takes
an ``ACCESS EXCLUSIVE`` lock — everything else waits. Not appropriate
for a scheduled job that runs while the pipeline is live. Plain
``VACUUM`` is non-blocking and still reclaims space for future row
writes within the table.

## Config (``plugin.job.postgres_vacuum``)

- ``enabled`` (default true)
- ``config.tables`` — list of table names to vacuum. Default covers
  the high-churn set. Operators with extra tables can extend.
- ``config.statement_timeout_seconds`` (default 1800) — per-table
  cap. A healthy vacuum completes in seconds to a minute at our scale;
  a 30 min cap catches runaway locks without killing a legit vacuum
  on a huge table.

No try/except eating — errors surface in the JobResult detail per
Matt's policy.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


DEFAULT_TABLES: tuple[str, ...] = (
    "embeddings",          # grows N rows per auto-embed cycle
    "audit_log",           # append-only, millions of rows
    "cost_logs",           # append-only, per-LLM-call
    "pipeline_tasks",      # status churns (draft → awaiting → published)
    "content_tasks",       # same
    "page_views",          # append-only, per-request
    "app_settings",        # small but value-updates happen
)


# Safe identifier regex: lowercase letters, digits, underscores. PostgreSQL
# allows more but we restrict on purpose — any table name containing a
# quote or semicolon is a config bug.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class PostgresVacuumJob:
    name = "postgres_vacuum"
    description = "VACUUM (ANALYZE) high-churn tables to reclaim dead-tuple space and refresh planner stats"
    schedule = "every 6 hours"
    idempotent = True  # VACUUM is inherently safe to re-run

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        tables_cfg = config.get("tables") or list(DEFAULT_TABLES)
        if not isinstance(tables_cfg, list):
            return JobResult(
                ok=False,
                detail=f"config.tables must be a list, got {type(tables_cfg).__name__}",
                changes_made=0,
            )

        # Reject unsafe identifiers before we interpolate them into SQL.
        # VACUUM doesn't support parameterized table names, so we have
        # to build the SQL string. The regex + explicit reject is the
        # security boundary.
        for name in tables_cfg:
            if not isinstance(name, str) or not _SAFE_IDENT.match(name):
                return JobResult(
                    ok=False,
                    detail=f"unsafe table name {name!r} — must match [a-z_][a-z0-9_]*",
                    changes_made=0,
                )

        statement_timeout_s = int(config.get("statement_timeout_seconds", 1800))

        results: list[tuple[str, float, bool, str]] = []
        total_duration = 0.0
        success_count = 0

        async with pool.acquire() as conn:
            # Per-connection statement_timeout guards against a
            # pathologically slow VACUUM locking something up.
            await conn.execute(
                f"SET statement_timeout = {statement_timeout_s * 1000}"
            )

            for table in tables_cfg:
                start = time.monotonic()
                error_msg = ""
                ok = False
                # Narrow except: surface per-table failures without
                # aborting the whole job. Each table's status ends up
                # in the result detail so the operator sees what
                # failed. Matches Matt's "no eating errors" — we
                # log + report, we don't silently continue.
                try:
                    await conn.execute(f"VACUUM (ANALYZE) {table}")
                    ok = True
                except Exception as e:
                    error_msg = str(e)[:200]
                    logger.warning(
                        "PostgresVacuumJob: VACUUM %s failed: %s",
                        table, error_msg,
                    )

                duration = time.monotonic() - start
                total_duration += duration
                if ok:
                    success_count += 1
                results.append((table, duration, ok, error_msg))
                logger.info(
                    "PostgresVacuumJob: %s -> %.2fs ok=%s",
                    table, duration, ok,
                )

        summary_parts = [
            f"{table}={'ok' if ok else 'FAIL'}({dur:.1f}s)"
            + (f":{err[:50]}" if err else "")
            for table, dur, ok, err in results
        ]
        detail = (
            f"vacuumed {success_count}/{len(tables_cfg)} tables in "
            f"{total_duration:.1f}s: " + ", ".join(summary_parts)
        )
        logger.info("PostgresVacuumJob: %s", detail)

        return JobResult(
            ok=(success_count == len(tables_cfg)),
            detail=detail,
            changes_made=success_count,
        )
