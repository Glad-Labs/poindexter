"""Handler: ``outbound.telegram_post``.

Sends a Telegram message via the Bot API ``sendMessage`` endpoint.
Telegram isn't a webhook-style destination (no fire-and-forget URL);
instead the handler wraps the Bot API call so from the dispatcher's
perspective it's one more outbound integration row.

Payload shape:

.. code:: python

    {"text": "message body"}

or a plain string — the handler coerces both.

Row configuration:

- ``url`` — must be set to the Bot API base
  ``https://api.telegram.org``. The handler composes the path.
- ``signing_algorithm`` — always ``bearer``; the bot token is the
  bearer auth for the Bot API.
- ``secret_key_ref`` — app_settings key holding the bot token
  (typically ``telegram_bot_token``, encrypted).
- ``config.chat_id`` — which chat to send to. Read at dispatch time
  from the row's ``config`` JSONB.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.integrations.registry import register_handler
from services.integrations.secret_resolver import resolve_secret

logger = logging.getLogger(__name__)


@register_handler("outbound", "telegram_post")
async def telegram_post(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Send a message via Telegram Bot API."""
    base_url = (row.get("url") or "https://api.telegram.org").rstrip("/")

    text: str
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, dict) and "text" in payload:
        text = str(payload["text"])
    else:
        raise TypeError(
            "telegram_post: payload must be str or dict with 'text' key"
        )

    bot_token = await resolve_secret(row, site_config)
    if not bot_token:
        raise RuntimeError(
            "telegram_post: bot token not configured "
            "(set secret_key_ref or populate the referenced app_settings key)"
        )
    bot_token = bot_token.strip()

    config = row.get("config") or {}
    if isinstance(config, dict):
        chat_id = config.get("chat_id")
    else:
        chat_id = None
    if not chat_id:
        raise RuntimeError(
            "telegram_post: config.chat_id is required on the row"
        )

    url = f"{base_url}/bot{bot_token}/sendMessage"
    body = {"chat_id": chat_id, "text": text}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body)

    if response.status_code != 200:
        raise RuntimeError(
            f"telegram_post: HTTP {response.status_code}: {response.text[:200]}"
        )

    logger.debug(
        "[outbound.telegram_post] delivered to chat %s (row=%s)",
        chat_id, row.get("name"),
    )
    return {"status_code": response.status_code, "chat_id": str(chat_id)}
