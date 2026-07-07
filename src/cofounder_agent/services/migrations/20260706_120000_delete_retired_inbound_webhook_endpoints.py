"""Migration 20260706_120000: delete the retired inbound webhook_endpoints rows.

Convergence step for the ``webhook.*`` inbound-handler retirement. The three
inbound webhook handlers — ``alertmanager_dispatch`` / ``revenue_event_writer`` /
``subscriber_event_writer`` — were orphaned when the generic inbound
``services/integrations/webhook_dispatcher.py`` was deleted 2026-05-09: nothing
dispatched them afterward (``registry.dispatch("webhook", …)`` has no caller),
and the live inbound webhooks are served by dedicated routes
(``routes/alertmanager_webhook_routes.py`` + ``routes/external_webhooks.py``).
The handler modules and their baseline seed rows were removed in the same PR.

**Why this migration exists.** The Phase F baseline now seeds only the two
OUTBOUND ``webhook_endpoints`` rows, but a baseline ``INSERT … ON CONFLICT DO
NOTHING`` can't REMOVE a row a prior baseline already seeded on an existing
install. So prod still carries the three vestigial inbound rows. This is the
convergence step:

  * Fresh installs — the Phase F seeds never insert them, so the DELETE matches
    0 rows (no-op).
  * Existing installs (prod) — this deletes the three vestigial inbound rows.

The rows were inert (no inbound dispatcher consumed them; no startup validation
ties ``webhook_endpoints.handler_name`` to the registry), so removing them
changes no behavior — it only stops the Integrations & Admin dashboard from
listing three "inbound webhook" rows that point at handlers that no longer
exist.

Scoped by exact id AND ``direction = 'inbound'`` so it can never touch the two
OUTBOUND apprise rows (``discord_ops`` / ``telegram_ops``), which stay.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM webhook_endpoints
            WHERE direction = 'inbound'
              AND id IN (
                '7f6a4065-37f8-446d-b18e-385467842467',  -- alertmanager  -> alertmanager_dispatch
                '1daf5274-b892-45d6-bff7-45e80520b302',  -- lemon_squeezy -> revenue_event_writer
                'a56f2cdd-3b38-4173-9b5e-e1c5b20404a0'   -- resend        -> subscriber_event_writer
              )
            """
        )
    logger.info(
        "delete_retired_inbound_webhook_endpoints up: %s "
        "(no-op where already absent, e.g. fresh installs)",
        result,
    )


async def down(pool) -> None:
    # No-op: these inbound rows are retired (their handlers were deleted; the
    # generic inbound dispatcher was removed 2026-05-09). Re-inserting them would
    # only restore vestigial config pointing at handlers that no longer exist.
    # Same one-way posture as 20260622_200222_drop_pipeline_tasks_category.
    logger.info(
        "delete_retired_inbound_webhook_endpoints down: no-op "
        "(refusing to re-add retired inbound webhook rows)"
    )
