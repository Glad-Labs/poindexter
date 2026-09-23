"""Migration: subscriber_events.provider_message_id + unique index

ISSUE: Glad-Labs/glad-labs-stack#3216 (follow-on)

``subscriber_events`` was fed only by ``POST /api/webhooks/resend``, which
is unreachable from the internet — the route answers 401 locally and 404
publicly. Receipts stopped arriving 2026-07-19 while the newsletter kept
sending (``campaign_email_logs`` shows 70 delivered through 2026-09-22),
so the table looked dead while the feature it reports on was healthy.

The replacement producer is a poll: Resend's ``GET /emails`` returns
``last_event`` for each sent message, so delivery state is readable with
no ingress at all — the same finding that replaced the Lemon Squeezy
webhook with an invoice poll (stack#3954).

A poll needs a stable key to be idempotent. Resend's email id is that key,
so this adds the column and a partial unique index on
``(provider_message_id, event_type)`` — one row per (message, state), which
lets a re-poll be ``ON CONFLICT DO NOTHING`` instead of a racing
``NOT EXISTS``. Partial, so the historical webhook rows (which carry no
provider id) keep their NULLs without colliding with each other.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE subscriber_events "
            "ADD COLUMN IF NOT EXISTS provider_message_id text"
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_subscriber_events_provider_event
                ON subscriber_events (provider_message_id, event_type)
             WHERE provider_message_id IS NOT NULL
            """
        )
    logger.info(
        "Migration 20260923: subscriber_events.provider_message_id + "
        "ux_subscriber_events_provider_event in place"
    )


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS ux_subscriber_events_provider_event")
        await conn.execute(
            "ALTER TABLE subscriber_events DROP COLUMN IF EXISTS provider_message_id"
        )
    logger.info("Migration 20260923: reverted")
