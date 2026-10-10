"""Migration 20260922_181657: revenue_events external_id unique index + drop LS test rows

ISSUE: Glad-Labs/poindexter#3216

``revenue_events`` carried no uniqueness on ``external_id`` — only
``PK(id)`` plus non-unique indexes on created_at/type/post. Every writer
therefore had to guard itself, and the one that existed
(``pro_delivery._record_initial_revenue``) used ``WHERE NOT EXISTS``,
which is a TOCTOU race on a job that fires every 5 minutes and can
overlap itself. This makes idempotency structural instead: the
invoice-driven ledger writes ``ON CONFLICT DO NOTHING`` against a real
constraint, the same shape as ``ux_social_post_drafts_active_key``.

The partial index (``WHERE external_id IS NOT NULL``) leaves room for
future revenue sources that have no external identity — affiliate or
ad rows are aggregates, not third-party objects, so several may legitimately
carry NULL.

Also removes the two non-revenue rows that predate a live checkout, both
verified test data rather than sales (glad-labs-stack#3216 audit):

- ``test-poindexter-1`` — the 2026-04-25 hand-POSTed webhook payload
  (``test@gladlabs.io``, $29.00 fabricated).
- ``ls_order_9315803`` — a $0 self-test subscription the operator bought
  from their own store to exercise the pay-to-delivery chain (2026-08-27,
  expired 2026-09-03). Its initial charge is now sourced from
  ``/v1/subscription-invoices`` instead, so leaving it would double-count
  against the invoice-keyed row.

Deleting them leaves the money table empty and honest before the first
real sale, and lets the invoice poll be authoritative from its first run.
Both DELETEs are keyed to the exact external ids, so the statement cannot
over-delete on an install that never had them (where it is a no-op).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The two pre-checkout non-revenue rows. Keyed exactly so this can never
# reach a real sale, and so a fresh install no-ops cleanly.
_TEST_EXTERNAL_IDS = ("test-poindexter-1", "ls_order_9315803")


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        deleted = await conn.execute(
            "DELETE FROM revenue_events WHERE external_id = ANY($1::text[])",
            list(_TEST_EXTERNAL_IDS),
        )
        # Duplicates would make the unique index build fail. There should be
        # none (the only writer guarded itself), but a pre-existing duplicate
        # must surface as a loud, actionable error rather than a cryptic
        # index-build failure.
        dupes = await conn.fetch(
            """
            SELECT external_id, count(*) AS n
              FROM revenue_events
             WHERE external_id IS NOT NULL
             GROUP BY external_id
            HAVING count(*) > 1
            """
        )
        if dupes:
            listed = ", ".join(f"{r['external_id']} x{r['n']}" for r in dupes[:5])
            raise RuntimeError(
                "revenue_events has duplicate external_id rows; resolve them "
                f"before the unique index can build: {listed}"
            )

        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_revenue_events_external_id
                ON revenue_events (external_id)
             WHERE external_id IS NOT NULL
            """
        )
    logger.info(
        "Migration 20260922_181657: dropped LS test rows (%s), "
        "ux_revenue_events_external_id in place",
        deleted,
    )


async def down(pool) -> None:
    """Revert the migration.

    Drops the index only. The deleted rows are NOT restored: both were
    fabricated test data, and re-inserting invented money into the revenue
    ledger would be worse than the one-way loss.
    """
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS ux_revenue_events_external_id")
    logger.info("Migration 20260922_181657: reverted (index dropped)")
