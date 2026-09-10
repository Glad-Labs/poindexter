"""
Retention janitor — legacy background loop, now a noop executor.

## Status: consolidated into retention_policies (Glad-Labs/poindexter#699)

All per-table retention windows were migrated into ``retention_policies``
rows (migration ``20260617_200000_seed_retention_policies_unified.py``) and
are now executed by ``RunRetentionJob`` via ``retention_runner.run_all()``
every 6 hours. The ``_JANITOR_TARGETS`` list is intentionally empty — the
janitor's ``run_once`` is a safe noop that returns ``{}`` immediately.

The background loop (``run_forever``) is still started by
``startup_manager`` (harmless: it sleeps and does nothing), and the
``AppContainer.retention_janitor`` cached property is preserved for
callers that hold a reference. Neither will be removed until a future
cleanup pass removes the startup wiring.

## Why two systems existed

The original janitor (Phase 4.1) pre-dated the declarative
``retention_policies`` / ``retention_runner`` framework. Both ran
concurrently with different intervals and conflicting windows on the same
tables (e.g. ``audit_log`` was 90d summarize-first in the declarative
pipeline vs. 180d hard-delete in the janitor). That race could destroy
rows the declarative pipeline intended to summarize first.

## Usage

    from services.retention_janitor import RetentionJanitor

    janitor = RetentionJanitor(site_config=site_config)
    await janitor.run_once(pool)    # returns {} — noop
    await janitor.run_forever(pool) # sleeps indefinitely, never pruning

2026-05-29 — SiteConfig DI migration (#272 leaf batch 3) converted this
module to a ``RetentionJanitor`` class with constructor DI.
2026-06-17 — #699 emptied ``_JANITOR_TARGETS``; all tables moved to the
declarative pipeline.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


# Intentionally empty — all tables migrated to retention_policies rows
# (migration 20260617_200000_seed_retention_policies_unified.py) and
# executed by RunRetentionJob via retention_runner.run_all() every 6h.
# Keeping the symbol avoids breaking callers that import it (e.g. tests).
_JANITOR_TARGETS: list[tuple[str, str, int]] = []


class RetentionJanitor:
    """Periodic pruner for unbounded high-churn tables.

    Constructed by ``AppContainer.retention_janitor`` per the SiteConfig
    constructor-DI migration. Reads per-table ``retention_days__<table>``
    keys and the ``retention_janitor_interval_hours`` loop interval from
    the injected ``SiteConfig``.
    """

    def __init__(self, *, site_config: SiteConfig) -> None:
        self._site_config = site_config

    def _retention_days_for(self, table: str, default_days: int) -> int:
        """Resolve retention window for a single table.

        ``retention_days__<table>`` is the canonical key. A missing or zero
        value disables pruning for that table (NOT "prune everything"). Zero
        means "skip" because zero-day retention is dangerous and never what
        an operator wants by accident.
        """
        key = f"retention_days__{table}"
        value = self._site_config.get(key, "")
        if not value:
            return default_days
        try:
            n = int(value)
        except (TypeError, ValueError):
            logger.warning(
                "[retention_janitor] %s is not an integer: %r — falling back to default",
                key, value,
            )
            return default_days
        return n

    async def _prune_one(self, pool: Any, table: str, ts_col: str, days: int) -> int:
        """Delete rows older than ``days`` from ``table``. Returns rows affected."""
        if days <= 0:
            logger.debug("[retention_janitor] %s: retention=0 — skip", table)
            return 0
        sql = (
            f"DELETE FROM {table} "  # noqa: S608  # nosec B608  # table is a whitelisted constant from _JANITOR_TARGETS
            f"WHERE {ts_col} < (NOW() - INTERVAL '{int(days)} days')"
        )
        async with pool.acquire() as conn:
            result = await conn.execute(sql)
            # asyncpg returns "DELETE N"
            try:
                deleted = int(result.split()[-1])
            except (ValueError, IndexError):
                deleted = 0
            return deleted

    async def run_once(self, pool: Any) -> dict[str, int]:
        """Run a single pass over every janitor target. Returns per-table
        rows-deleted counts. Never raises — pruning is additive ops; a
        single-table failure shouldn't tank the cycle.
        """
        results: dict[str, int] = {}
        for table, ts_col, default_days in _JANITOR_TARGETS:
            try:
                days = self._retention_days_for(table, default_days)
                deleted = await self._prune_one(pool, table, ts_col, days)
                results[table] = deleted
                if deleted:
                    logger.info(
                        "[retention_janitor] %s: deleted %s rows older than %sd",
                        table, deleted, days,
                    )
            except Exception as exc:
                logger.warning(
                    "[retention_janitor] %s prune failed (non-fatal): %s", table, exc,
                )
                results[table] = -1
        return results

    async def run_forever(
        self,
        pool: Any,
        *,
        interval_hours_default: float = 24.0,
    ) -> None:
        """Long-running loop — awaits ``retention_janitor_interval_hours``
        between cycles. Intended to be launched as a background task from
        startup_manager.
        """
        while True:
            try:
                hours_raw = self._site_config.get(
                    "retention_janitor_interval_hours", str(interval_hours_default),
                )
                hours = float(hours_raw or interval_hours_default)
            except (TypeError, ValueError):
                hours = interval_hours_default
            sleep_s = max(60.0, hours * 3600.0)

            started = datetime.now(timezone.utc)
            try:
                results = await self.run_once(pool)
                total_deleted = sum(v for v in results.values() if v > 0)
                logger.info(
                    "[retention_janitor] Cycle complete: %s total rows deleted across %s tables (started %s)",
                    total_deleted, len([v for v in results.values() if v >= 0]), started.isoformat(),
                )
            except Exception as exc:
                logger.warning(
                    "[retention_janitor] Cycle raised (non-fatal): %s", exc,
                )
            await asyncio.sleep(sleep_s)
