"""Migration 0158: task-failure alert dedup + severity routing + backoff settings.

Glad-Labs/poindexter#370 — a single failing task produced 8+ Telegram
alerts in 35 seconds tonight. Two latent bugs converged:

(a) ``_auto_retry_failed_tasks`` re-claims ``failed`` rows by flipping
    them back to ``pending`` every sweep cycle (5 min). With the default
    ``max_task_retries=3`` and a fast-failing condition, the same task
    runs four times back-to-back.
(b) Each pass calls ``notify_operator(critical=True)`` from the failure
    path with no dedup, so every attempt pages Telegram — the channel
    Matt's phone gets push notifications for. Per
    ``feedback_telegram_vs_discord``, Telegram is reserved for the
    explicit critical-alert list; routine task failures belong on
    Discord.

This migration seeds the four new app_settings keys the runtime reads
and creates a persistent dedup table so the dedup window survives a
worker restart (a fast crash-loop on the worker would otherwise reset
the in-memory LRU and re-page on every restart):

* ``task_failure_alert_dedup_window_seconds`` (int, default 900) —
  suppress duplicate alerts for the same ``(task_id, error_hash)``
  inside this window.
* ``task_failure_alert_severity`` (``'discord'`` | ``'telegram'``,
  default ``'discord'``) — channel the routine task-failure alert
  routes to. Telegram stays opt-in for operators who genuinely want
  every failure to page their phone.
* ``task_retry_max_attempts`` (int, default 0) — max number of
  automatic re-claims of a ``failed`` task. Default ``0`` disables
  the auto-retry sweeper entirely; the operator must explicitly
  retry. ``max_task_retries`` is the legacy stale-sweep ceiling and
  is left untouched.
* ``task_retry_backoff_initial_seconds`` (int, default 60) —
  exponential-backoff base for the auto-retry sweeper. Attempt N
  waits ``backoff * 2^(N-1)`` seconds since the last failure before
  becoming eligible. Only meaningful when
  ``task_retry_max_attempts > 0``.

The dedup table schema is intentionally tiny: one row per
``(task_id, error_hash)``, last-sent timestamp, attempt count. The
runtime upserts on every alert and gates on ``last_sent_at + window``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SETTINGS: list[tuple[str, str, str, str]] = [
    # (key, value, category, description)
    (
        "task_failure_alert_dedup_window_seconds",
        "900",
        "alerts",
        "Suppress duplicate task-failure alerts for the same "
        "(task_id, error_message_hash) within this window. Default 900 "
        "(15 min). Set to 0 to disable dedup. Per Glad-Labs/poindexter#370.",
    ),
    (
        "task_failure_alert_severity",
        "discord",
        "alerts",
        "Channel routine task-failure alerts route to: 'discord' (default, "
        "the spam channel) or 'telegram' (escalates to operator's phone). "
        "Per feedback_telegram_vs_discord, Telegram is reserved for the "
        "critical-alert list (worker offline, GPU temp, cost overrun, "
        "failure-rate breach) — a single bad task should not page the "
        "operator. Glad-Labs/poindexter#370.",
    ),
    (
        "task_retry_max_attempts",
        "0",
        "alerts",
        "Max automatic re-claims of a 'failed' task by "
        "_auto_retry_failed_tasks. Default 0 disables auto-retry "
        "entirely; operator must explicitly retry via /retry-task. "
        "Setting to N > 0 re-enables the legacy retry-with-adjustments "
        "behavior, capped at N attempts. Glad-Labs/poindexter#370.",
    ),
    (
        "task_retry_backoff_initial_seconds",
        "60",
        "alerts",
        "Exponential-backoff base for auto-retry. Attempt N becomes "
        "eligible only after backoff * 2^(N-1) seconds have passed "
        "since the last failure. Only meaningful when "
        "task_retry_max_attempts > 0. Glad-Labs/poindexter#370.",
    ),
]


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. Dedup table — persistent so a worker restart doesn't
        # clear the suppression window. Indexed on the lookup key so
        # the per-alert UPSERT is O(1).
        if not await _table_exists(conn, "task_failure_alerts"):
            await conn.execute(
                """
                CREATE TABLE task_failure_alerts (
                  task_id        TEXT NOT NULL,
                  error_hash     TEXT NOT NULL,
                  last_sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  alert_count    INTEGER NOT NULL DEFAULT 1,
                  last_error     TEXT,
                  last_severity  TEXT,
                  PRIMARY KEY (task_id, error_hash)
                )
                """
            )
            # Cheap pruning helper: orderable by last_sent_at so the
            # daily retention sweeper can delete old rows in one query.
            await conn.execute(
                "CREATE INDEX idx_task_failure_alerts_last_sent "
                "ON task_failure_alerts (last_sent_at DESC)"
            )
            logger.info(
                "Migration 0158: created task_failure_alerts table + index"
            )
        else:
            logger.info(
                "Migration 0158: task_failure_alerts already exists — skipping create"
            )

        # 2. Seed the new app_settings keys.
        for key, value, category, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            logger.info("Migration 0158: seeded %s = %s", key, value)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS task_failure_alerts")
        for key, *_ in _SETTINGS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
        logger.info(
            "Migration 0158 down: dropped task_failure_alerts table + "
            "removed alert dedup/backoff seeds"
        )
