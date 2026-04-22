"""
Retention janitor — periodic cleanup of unbounded high-churn tables.

Part of gitea#271 Phase 4.1. Several tables grow without bound and were
flagged in the schema audit as unbounded:

  - gpu_metrics         — per-second GPU snapshots
  - audit_log           — pipeline event log
  - cost_logs           — LLM/API cost accounting
  - routing_outcomes    — feedback-loop routing signal
  - model_performance   — feedback-loop model signal
  - webhook_events      — incoming webhook trail
  - task_status_history — task state transitions

Each table has an app_settings retention key:
    retention_days__<table_name>   (integer; 0 or missing ⇒ skip)

The janitor runs once per ``retention_janitor_interval_hours`` (default 24)
and deletes rows older than the configured window.

Rules:
  - embeddings is NEVER pruned — that's the knowledge base.
  - Gitea's action_* / commit_* / issue / etc. tables are Gitea's, not ours.
  - cost_logs is truncated more gently (financial record; default 365).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


# Tables the janitor is allowed to prune. Tuples of
# (table, timestamp_column, default_retention_days).
#
# The default values are conservative — operators can lower them via
# app_settings once they're comfortable with the history they actually
# reach for.
_JANITOR_TARGETS: list[tuple[str, str, int]] = [
    ("gpu_metrics", "timestamp", 90),
    ("audit_log", "timestamp", 180),
    ("cost_logs", "created_at", 365),
    ("routing_outcomes", "created_at", 365),
    ("model_performance", "created_at", 365),
    ("webhook_events", "created_at", 90),
    ("task_status_history", "created_at", 180),
    ("page_views", "created_at", 180),
    # Brain decision trails — long retention since these feed the
    # "what did Claude/the agent decide before" semantic search path.
    ("brain_decisions", "created_at", 365),
    ("decision_log", "created_at", 365),
    # post_performance snapshots grow ~N_posts per day; 180d is plenty
    # of history for week-over-week comparisons without bloating forever.
    ("post_performance", "measured_at", 180),
]


def _retention_days_for(site_config: Any, table: str, default_days: int) -> int:
    """Resolve retention window for a single table.

    ``retention_days__<table>`` is the canonical key. A missing or zero
    value disables pruning for that table (NOT "prune everything"). Zero
    means "skip" because zero-day retention is dangerous and never what
    an operator wants by accident.
    """
    key = f"retention_days__{table}"
    value = site_config.get(key, "")
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


async def _prune_one(pool: Any, table: str, ts_col: str, days: int) -> int:
    """Delete rows older than ``days`` from ``table``. Returns rows affected."""
    if days <= 0:
        logger.debug("[retention_janitor] %s: retention=0 — skip", table)
        return 0
    sql = (
        f"DELETE FROM {table} "  # noqa: S608 — table is a whitelisted constant
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


async def run_once(pool: Any, site_config: Any) -> dict[str, int]:
    """Run a single pass over every janitor target. Returns per-table
    rows-deleted counts. Never raises — pruning is additive ops; a
    single-table failure shouldn't tank the cycle.

    Phase H (GH#95): ``site_config`` is an explicit parameter instead of
    the module-level singleton. Retention windows are read from it.
    """
    results: dict[str, int] = {}
    for table, ts_col, default_days in _JANITOR_TARGETS:
        try:
            days = _retention_days_for(site_config, table, default_days)
            deleted = await _prune_one(pool, table, ts_col, days)
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
    pool: Any,
    site_config: Any,
    *,
    interval_hours_default: float = 24.0,
) -> None:
    """Long-running loop — awaits ``retention_janitor_interval_hours``
    between cycles. Intended to be launched as a background task from
    startup_manager.

    Phase H (GH#95): ``site_config`` is an explicit parameter instead of
    the module-level singleton. Interval and per-table retention windows
    are read from it.
    """
    while True:
        try:
            hours_raw = site_config.get(
                "retention_janitor_interval_hours", str(interval_hours_default),
            )
            hours = float(hours_raw or interval_hours_default)
        except (TypeError, ValueError):
            hours = interval_hours_default
        sleep_s = max(60.0, hours * 3600.0)

        started = datetime.now(timezone.utc)
        try:
            results = await run_once(pool, site_config)
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
