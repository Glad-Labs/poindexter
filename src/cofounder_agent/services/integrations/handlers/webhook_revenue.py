"""Handler: ``webhook.revenue_event_writer``.

Parses a Lemon Squeezy-shaped payload and inserts a row into
``revenue_events``. Migrated from ``routes/external_webhooks.py``
(gitea#271 Phase 3.B) onto the declarative framework.

See ``docs/integrations/webhook_revenue_event_writer.md`` for the
operator setup runbook.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


# Which attribute on the data.attributes object carries the total.
# Subscription updates/cancels still carry `total` as the current
# period's charge, so a single key covers the payload shapes we see.
_AMOUNT_KEYS: dict[str, str] = {
    "order_created": "total",
    "order_refunded": "total",
    "subscription_created": "total",
    "subscription_updated": "total",
    "subscription_cancelled": "total",
}


@register_handler("webhook", "revenue_event_writer")
async def revenue_event_writer(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Insert a row into ``revenue_events`` from a Lemon Squeezy payload."""
    if not isinstance(payload, dict):
        return {"event": "ignored", "reason": "non-dict payload"}

    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}

    event_type = meta.get("event_name") or "unknown"
    amount_key = _AMOUNT_KEYS.get(event_type, "total")
    cents = attrs.get(amount_key) or 0
    try:
        amount_usd = float(cents) / 100.0
    except (TypeError, ValueError):
        amount_usd = 0.0

    # Refunds negate the amount so SUM(amount_usd) gives net revenue.
    if event_type == "order_refunded":
        amount_usd = -abs(amount_usd)

    recurring = event_type.startswith("subscription_")
    customer_email = attrs.get("user_email")
    customer_id = str(data.get("id")) if data.get("id") is not None else None
    external_id = meta.get("webhook_id") or str(data.get("id"))

    if pool is None:
        raise RuntimeError("database pool unavailable")

    await pool.execute(
        """
        INSERT INTO revenue_events (
            event_type, source, amount_usd, currency, recurring,
            customer_email, customer_id, external_id, external_data
        )
        VALUES ($1, 'lemon_squeezy', $2, 'USD', $3, $4, $5, $6, $7)
        """,
        event_type,
        amount_usd,
        recurring,
        customer_email,
        customer_id,
        external_id,
        json.dumps(payload),
    )

    logger.info(
        "[webhook.revenue_event_writer] %s recorded ($%.2f, recurring=%s)",
        event_type, amount_usd, recurring,
    )
    return {
        "event": event_type,
        "amount_usd": amount_usd,
        "recurring": recurring,
    }
