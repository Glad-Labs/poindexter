"""Retention runner — walk enabled policies and execute their handlers.

Single entry point for lifecycle management. Intended to be called
once a day by the scheduler (or on demand via ``poindexter retention
run``). For each enabled row in ``retention_policies``, looks up the
handler by name, invokes it with the row as config, and records
success/failure counters on the row.

Isolation: a failure on one policy doesn't kill the run — the runner
records the error on that row and continues. The policy's
``last_error`` surfaces the issue on the Integration Health dashboard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from services.integrations import registry

logger = logging.getLogger(__name__)


@dataclass
class PolicyResult:
    name: str
    ok: bool
    duration_ms: int
    deleted: int = 0
    summarized: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    policies: list[PolicyResult]
    total_deleted: int
    total_summarized: int
    total_failed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "policies": [p.__dict__ for p in self.policies],
            "total_deleted": self.total_deleted,
            "total_summarized": self.total_summarized,
            "total_failed": self.total_failed,
        }


async def run_all(
    pool: Any,
    *,
    site_config: Any = None,
    only_names: list[str] | None = None,
) -> RunSummary:
    """Execute every enabled retention policy once.

    Args:
        pool: asyncpg pool for row lookup + handler dispatch.
        site_config: SiteConfig (handlers that need external config).
        only_names: restrict to these row names (CLI "run <name>" path).

    Returns a :class:`RunSummary` describing per-policy outcomes.
    """
    rows = await _load_enabled_policies(pool, only_names)
    results: list[PolicyResult] = []
    total_deleted = total_summarized = total_failed = 0

    for row in rows:
        name = row["name"]
        start = time.perf_counter()
        try:
            result = await registry.dispatch(
                "retention",
                row["handler_name"],
                None,  # payload unused for retention handlers
                site_config=site_config,
                row=dict(row),
                pool=pool,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            deleted = int(result.get("deleted", 0)) if isinstance(result, dict) else 0
            summarized = (
                int(result.get("summarized", 0)) if isinstance(result, dict) else 0
            )
            total_deleted += deleted
            total_summarized += summarized
            await _record_success(pool, row["id"], duration_ms, deleted, summarized)
            results.append(
                PolicyResult(
                    name=name, ok=True, duration_ms=duration_ms,
                    deleted=deleted, summarized=summarized,
                )
            )
            logger.info(
                "[retention-runner] %s: deleted=%d summarized=%d (%dms)",
                name, deleted, summarized, duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            total_failed += 1
            err = f"{type(exc).__name__}: {exc}"
            await _record_failure(pool, row["id"], duration_ms, err)
            results.append(
                PolicyResult(
                    name=name, ok=False, duration_ms=duration_ms, error=err,
                )
            )
            logger.warning(
                "[retention-runner] %s failed: %s", name, err, exc_info=True,
            )

    return RunSummary(
        policies=results,
        total_deleted=total_deleted,
        total_summarized=total_summarized,
        total_failed=total_failed,
    )


async def _load_enabled_policies(
    pool: Any, only_names: list[str] | None,
) -> list[dict[str, Any]]:
    if pool is None:
        return []
    if only_names:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM retention_policies
                 WHERE enabled = TRUE AND name = ANY($1::text[])
              ORDER BY name
                """,
                only_names,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM retention_policies
                 WHERE enabled = TRUE
              ORDER BY name
                """,
            )
    return [dict(r) for r in rows]


async def _record_success(
    pool: Any,
    row_id: Any,
    duration_ms: int,
    deleted: int,
    summarized: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE retention_policies
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_deleted = $3,
                   last_run_summarized = $4,
                   last_error = NULL,
                   total_runs = total_runs + 1,
                   total_deleted = total_deleted + $3
             WHERE id = $1
            """,
            row_id, duration_ms, deleted, summarized,
        )


async def _record_failure(
    pool: Any,
    row_id: Any,
    duration_ms: int,
    error: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE retention_policies
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_error = $3,
                   total_runs = total_runs + 1
             WHERE id = $1
            """,
            row_id, duration_ms, error,
        )
