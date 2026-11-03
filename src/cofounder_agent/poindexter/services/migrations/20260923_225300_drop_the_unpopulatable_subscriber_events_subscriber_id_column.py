"""Migration: drop subscriber_events.subscriber_id

ISSUE: Glad-Labs/poindexter#3216 (follow-on)

``subscriber_events.subscriber_id`` is a **uuid**. The only subscriber table,
``newsletter_subscribers``, has a **serial int** primary key. The two cannot
be joined, so nothing has ever been able to populate the column — and nothing
ever did: 75 rows, 0 non-NULL, across both the webhook era and the poll that
replaced it.

It was not merely unused, it was actively harmful. Building the Resend
delivery poll (stack#3971) I read the NULLs as the webhook path being sloppy
rather than as the schema being impossible, wrote the obvious lookup, and the
first production tick failed on all 19 messages with

    DataError: invalid input for query argument $1: 4
    ('int' object has no attribute 'bytes')

A column that cannot hold a value still advertises that it should, which is
what a reader acts on. ``email`` is the identity for this table and always
has been — every writer sets it.

Also drops ``idx_subscriber_events_subscriber``. Postgres would drop it with
the column anyway; naming it here keeps the intent readable and makes the
``down()`` symmetric. The index was partial (``WHERE subscriber_id IS NOT
NULL``) over a column that was always NULL, so it has indexed zero rows for
its entire existence.

No view depends on the column (checked against ``pg_depend``), and the other
``subscriber_id`` columns in the schema — ``campaign_email_logs`` (integer,
FK to ``newsletter_subscribers``) — are a different table and untouched.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        # Refuse to silently discard data if some future writer managed to
        # populate this between the audit and the deploy. It cannot happen
        # with the current type mismatch, but a DROP is one-way.
        populated = await conn.fetchval(
            "SELECT count(subscriber_id) FROM subscriber_events"
        )
        if populated:
            raise RuntimeError(
                f"subscriber_events.subscriber_id holds {populated} non-NULL "
                "value(s); the drop assumes it is empty. Investigate before "
                "re-running — this migration discards the column."
            )

        await conn.execute("DROP INDEX IF EXISTS idx_subscriber_events_subscriber")
        await conn.execute(
            "ALTER TABLE subscriber_events DROP COLUMN IF EXISTS subscriber_id"
        )
    logger.info("Migration 20260923_225300: dropped subscriber_events.subscriber_id")


async def down(pool) -> None:
    """Revert the migration.

    Restores the column and its index. The values are not restored because
    there were none — the column was NULL in all 75 rows at drop time.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE subscriber_events ADD COLUMN IF NOT EXISTS subscriber_id uuid"
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_subscriber_events_subscriber
                ON subscriber_events (subscriber_id)
             WHERE subscriber_id IS NOT NULL
            """
        )
    logger.info("Migration 20260923_225300: reverted")
