"""Tap runner — walk enabled external_taps rows and invoke each handler.

Same shape as :mod:`retention_runner`: one scheduled entry point,
per-row isolation so a bad tap doesn't kill the run, success/failure
counters recorded on the row.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from services.integrations import registry

logger = logging.getLogger(__name__)


@dataclass
class TapResult:
    name: str
    ok: bool
    duration_ms: int
    records: int = 0
    error: str | None = None


@dataclass
class RunSummary:
    taps: list[TapResult]
    total_records: int
    total_failed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "taps": [t.__dict__ for t in self.taps],
            "total_records": self.total_records,
            "total_failed": self.total_failed,
        }


async def run_all(
    pool: Any,
    *,
    site_config: Any = None,
    only_names: list[str] | None = None,
) -> RunSummary:
    """Execute every enabled tap once.

    Args:
        pool: asyncpg pool.
        site_config: passed through to handlers for credential resolution.
        only_names: restrict to specific tap names.

    Returns a :class:`RunSummary` describing per-tap outcomes.
    """
    rows = await _load_enabled_taps(pool, only_names)
    results: list[TapResult] = []
    total_records = total_failed = 0

    for row in rows:
        name = row["name"]
        start = time.perf_counter()
        try:
            result = await registry.dispatch(
                "tap",
                row["handler_name"],
                None,
                site_config=site_config,
                row=dict(row),
                pool=pool,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            records = int(result.get("records", 0)) if isinstance(result, dict) else 0
            total_records += records
            await _record_success(pool, row["id"], duration_ms, records)
            results.append(
                TapResult(
                    name=name, ok=True, duration_ms=duration_ms, records=records,
                )
            )
            logger.info(
                "[tap-runner] %s: records=%d (%dms)", name, records, duration_ms,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            total_failed += 1
            err = f"{type(exc).__name__}: {exc}"
            await _record_failure(pool, row["id"], duration_ms, err)
            results.append(
                TapResult(
                    name=name, ok=False, duration_ms=duration_ms, error=err,
                )
            )
            logger.warning("[tap-runner] %s failed: %s", name, err, exc_info=True)

    return RunSummary(
        taps=results,
        total_records=total_records,
        total_failed=total_failed,
    )


async def _load_enabled_taps(
    pool: Any, only_names: list[str] | None,
) -> list[dict[str, Any]]:
    if pool is None:
        return []
    if only_names:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM external_taps
                 WHERE enabled = TRUE AND name = ANY($1::text[])
              ORDER BY name
                """,
                only_names,
            )
    else:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM external_taps
                 WHERE enabled = TRUE
              ORDER BY name
                """,
            )
    # Same JSONB-string parsing fix as outbound_dispatcher and
    # retention_runner. Tap handlers expect dict-typed config/metadata
    # but asyncpg returns raw JSON strings without a registered codec.
    out = []
    for r in rows:
        d = dict(r)
        for k in ("config", "metadata"):
            v = d.get(k)
            if isinstance(v, str) and v:
                try:
                    d[k] = json.loads(v)
                except json.JSONDecodeError as exc:
                    # poindexter#455 — used to be silent. A malformed
                    # JSONB cell silently kept the string and tap
                    # handlers (which expect d["config"]["enabled"]
                    # etc) then either crashed with TypeError or quietly
                    # treated absent keys as defaults. Log so the bad
                    # row is identifiable; let handlers continue to
                    # raise downstream so the failure stays loud.
                    logger.warning(
                        "[tap-runner] tap %r has malformed JSONB in %r "
                        "(%s) — leaving raw string; handler will likely "
                        "raise downstream",
                        d.get("name"), k, exc,
                    )
        out.append(d)
    return out


async def _record_success(
    pool: Any, row_id: Any, duration_ms: int, records: int,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE external_taps
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_status = 'success',
                   last_run_records = $3,
                   last_error = NULL,
                   total_runs = total_runs + 1,
                   total_records = total_records + $3
             WHERE id = $1
            """,
            row_id, duration_ms, records,
        )


async def _record_failure(
    pool: Any, row_id: Any, duration_ms: int, error: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE external_taps
               SET last_run_at = now(),
                   last_run_duration_ms = $2,
                   last_run_status = 'failed',
                   last_error = $3,
                   total_runs = total_runs + 1
             WHERE id = $1
            """,
            row_id, duration_ms, error,
        )
