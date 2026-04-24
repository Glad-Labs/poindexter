"""Handler: ``webhook.subscriber_event_writer``.

Parses a Resend-shaped email event payload and inserts a row into
``subscriber_events``. Migrated from ``routes/external_webhooks.py``
(gitea#271 Phase 3.B) onto the declarative framework.

Supported event types: email.sent, email.delivered, email.opened,
email.clicked, email.bounced, email.complained.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


def _extract_email(data: dict[str, Any]) -> str | None:
    """Resend sends ``to`` as either a string or a list."""
    to = data.get("to")
    if isinstance(to, str):
        return to
    if isinstance(to, list) and to:
        return to[0] if isinstance(to[0], str) else None
    return None


@register_handler("webhook", "subscriber_event_writer")
async def subscriber_event_writer(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Insert a row into ``subscriber_events`` from a Resend payload."""
    if not isinstance(payload, dict):
        return {"event": "ignored", "reason": "non-dict payload"}

    event_type = payload.get("type") or "unknown"
    data = payload.get("data") or {}
    email = _extract_email(data)

    if pool is None:
        raise RuntimeError("database pool unavailable")

    await pool.execute(
        """
        INSERT INTO subscriber_events (email, event_type, event_data)
        VALUES ($1, $2, $3)
        """,
        email,
        event_type,
        json.dumps(payload),
    )

    logger.info(
        "[webhook.subscriber_event_writer] %s recorded for %s",
        event_type, email,
    )
    return {"event": event_type, "email": email}
