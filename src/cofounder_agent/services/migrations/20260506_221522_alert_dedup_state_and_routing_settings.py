"""Migration 20260506_221522: alert_dedup_state + severity-routing settings.

ISSUE: Glad-Labs/poindexter#420 — coalesce repeating brain alerts +
escalate to AI summary on threshold + severity-based channel routing.

Today's screenshot from Matt showed the same "service openclaw is down"
alert paging Telegram every 5 minutes for 30+ minutes. Two latent bugs:

(a) The ``brain/alert_dispatcher`` had no fingerprint-dedup, so every
    fire of an identical alert went out as if it were the first.
(b) ``brain.notify`` always hit Telegram + Discord, ignoring severity.
    Per ``feedback_telegram_vs_discord``, Telegram is reserved for
    critical alerts; warning/info should land on Discord only.

This migration plants the four operator-tunable knobs the runtime
reads (window, threshold, force-Telegram override, retention) and
creates a tiny persistent dedup-state table so a brain restart
mid-burst does NOT lose the suppression context (which would re-page
the operator on every restart):

* ``alert_repeat_suppress_window_minutes`` (int, default 30) — when
  a fingerprint last fired inside this window, the new fire is
  suppressed (counted, not dispatched).
* ``alert_repeat_summarize_threshold_minutes`` (int, default 30) —
  after a fingerprint has been firing continuously for this many
  minutes, the dispatcher escalates to an AI summary (one-shot per
  fingerprint, marked via ``summary_dispatched_at``).
* ``alert_force_telegram_event_types`` (CSV string, default empty) —
  comma-separated ``event_type`` (or ``alertname``) values that
  ALWAYS Telegram regardless of severity. Use for things like
  ``cost_guard_tripped`` or ``worker_crashed_unrecoverable`` that
  the operator wants on their phone even when severity isn't
  ``critical``. Empty default = no overrides.
* ``alert_dedup_state_retention_hours`` (int, default 168 = 7 days)
  — the existing retention janitor uses this to prune
  ``alert_dedup_state`` rows whose ``last_seen_at`` aged past the
  window. Keeps the table from growing unbounded as fingerprints
  churn over time.

The dedup-state schema is intentionally tiny: one row per
fingerprint. ``first_seen_at`` anchors the threshold escalation;
``last_seen_at`` anchors the suppression window; ``repeat_count``
feeds the AI summary's "fired N times in M minutes" line;
``summary_dispatched_at`` is set once the LLM summary fires so the
threshold doesn't re-trigger every cycle.

Idempotent: ``CREATE TABLE IF NOT EXISTS`` + ``ON CONFLICT (key) DO
NOTHING``. Re-running this migration on a stack that already has
operator-set values preserves them.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS: list[tuple[str, str, str]] = [
    (
        "alert_repeat_suppress_window_minutes",
        "30",
        "When a brain alert with the same fingerprint "
        "(source|severity|normalized_message) last fired inside this "
        "window, the new fire is counted (repeat_count++) but NOT "
        "dispatched. Default 30 = the same alert at most every 30 min "
        "until escalation. Set to 0 to disable dedup entirely (every "
        "fire dispatches). Glad-Labs/poindexter#420.",
    ),
    (
        "alert_repeat_summarize_threshold_minutes",
        "30",
        "After a fingerprint has been firing continuously for this many "
        "minutes (now - first_seen_at), the dispatcher escalates to an "
        "AI summary that counts repeats, lists time-correlated other "
        "alerts in the same window, and produces a paragraph diagnosis. "
        "One-shot per fingerprint (marked via summary_dispatched_at). "
        "Default 30 min. Glad-Labs/poindexter#420.",
    ),
    (
        "alert_force_telegram_event_types",
        "",
        "Comma-separated list of event_type (or alertname) values that "
        "always route to Telegram regardless of severity. Use for "
        "alerts like 'cost_guard_tripped,worker_crashed_unrecoverable' "
        "that the operator wants on their phone even at warning "
        "severity. Empty default = no overrides; pure severity routing "
        "applies (critical/error -> both, warning/info -> Discord "
        "only). Glad-Labs/poindexter#420.",
    ),
    (
        "alert_dedup_state_retention_hours",
        "168",
        "Retention horizon for alert_dedup_state rows. The retention "
        "janitor deletes rows whose last_seen_at aged past this many "
        "hours so the table doesn't grow unbounded as fingerprints "
        "churn over time. Default 168 = 7 days. "
        "Glad-Labs/poindexter#420.",
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
    """Apply the migration."""
    async with pool.acquire() as conn:
        # 1. Dedup-state table — persistent so a brain restart doesn't
        # clear the suppression window mid-burst (which would re-page
        # the operator on every restart). Indexed on last_seen_at so
        # the retention janitor can prune in one bounded query.
        if not await _table_exists(conn, "alert_dedup_state"):
            await conn.execute(
                """
                CREATE TABLE alert_dedup_state (
                    fingerprint           TEXT PRIMARY KEY,
                    first_seen_at         TIMESTAMPTZ NOT NULL,
                    last_seen_at          TIMESTAMPTZ NOT NULL,
                    repeat_count          INTEGER NOT NULL DEFAULT 1,
                    summary_dispatched_at TIMESTAMPTZ,
                    severity              TEXT NOT NULL,
                    source                TEXT NOT NULL,
                    sample_message        TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX idx_alert_dedup_state_last_seen "
                "ON alert_dedup_state (last_seen_at DESC)"
            )
            logger.info(
                "Migration 20260506_221522: created alert_dedup_state "
                "table + last_seen index"
            )
        else:
            logger.info(
                "Migration 20260506_221522: alert_dedup_state already "
                "exists -- skipping create"
            )

        # 2. Seed the four new app_settings keys. Empty CSV is allowed
        # for alert_force_telegram_event_types because app_settings.value
        # is NOT NULL but may be ''. See feedback_app_settings_value_not_null.
        if not await _table_exists(conn, "app_settings"):
            logger.info(
                "Migration 20260506_221522: app_settings missing -- "
                "skipping seeds (table will be created later)"
            )
            return
        inserted = 0
        for key, value, description in _SEEDS:
            result = await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, 'alerts', $3, FALSE, TRUE)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, description,
            )
            if result == "INSERT 0 1":
                inserted += 1
        logger.info(
            "Migration 20260506_221522: seeded %d/%d alert_* settings "
            "(remaining were already set)",
            inserted, len(_SEEDS),
        )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS alert_dedup_state")
        if await _table_exists(conn, "app_settings"):
            for key, _value, _description in _SEEDS:
                await conn.execute(
                    "DELETE FROM app_settings WHERE key = $1",
                    key,
                )
        logger.info(
            "Migration 20260506_221522 rolled back: dropped "
            "alert_dedup_state + removed %d alert_* seeds",
            len(_SEEDS),
        )
