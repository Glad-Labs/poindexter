"""Migration 20260531_005030_create_alert_actions_table_for_brain_auto_triage: create alert_actions table for brain auto-triage

ISSUE: Glad-Labs/poindexter#524 (self-healing audit follow-up)

The brain's ``monitor_services`` auto-triage (``brain/brain_daemon.py`` ~L1400)
looks up a remediation policy for a down-service ``pattern`` in an
``alert_actions`` table before escalating to the operator. That table was
never created (lost in the baseline squash or never migrated), so EVERY
critical-service-down event threw ``relation "alert_actions" does not exist``,
logged a full traceback, and fell back to a raw Telegram page. This created
recurring error-log noise and left the auto-triage feature dead.

This migration creates the empty ``alert_actions`` table with exactly the
columns the brain queries/updates. Empty means the lookup returns no row →
the brain escalates cleanly (no traceback). Operators (or a future seeder)
can INSERT pattern→action rows to enable cooldown-gated, escalate-after-N
auto-triage. The sibling ``alert_log`` table already exists.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration. Idempotent via ``CREATE TABLE IF NOT EXISTS``."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_actions (
                id                      SERIAL PRIMARY KEY,
                pattern                 TEXT NOT NULL UNIQUE,
                action_type             TEXT NOT NULL DEFAULT 'notify',
                cooldown_minutes        INTEGER NOT NULL DEFAULT 15,
                escalate_after_failures INTEGER NOT NULL DEFAULT 1,
                consecutive_failures    INTEGER NOT NULL DEFAULT 0,
                total_triggers          INTEGER NOT NULL DEFAULT 0,
                last_triggered_at       TIMESTAMPTZ,
                enabled                 BOOLEAN NOT NULL DEFAULT true,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # The brain filters on ``pattern = $1 AND enabled = true`` every cycle.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_actions_pattern_enabled "
            "ON alert_actions (pattern) WHERE enabled = true"
        )
        logger.info(
            "Migration create_alert_actions_table_for_brain_auto_triage: applied",
        )


async def down(pool) -> None:
    """Revert: drop the table (the brain tolerates its absence — it just
    falls back to direct escalation, which is the pre-migration behaviour)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS alert_actions")
        logger.info(
            "Migration create_alert_actions_table_for_brain_auto_triage down: reverted",
        )
