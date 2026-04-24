"""Handler: ``outbound.discord_post``.

Posts a message to a Discord webhook URL. Payload shape:

.. code:: python

    {
      "content": "message text",
      "embeds": [...]   # optional, Discord embed objects
    }

``content`` is the only required field. Matches the existing
``_notify_discord`` helper in :mod:`services.task_executor`, which
will migrate onto this handler in a follow-up.

Row configuration:

- ``url`` — the Discord webhook URL (the webhook URL itself is the
  bearer token for a Discord webhook, so ``signing_algorithm`` should
  be ``none`` and ``secret_key_ref`` unset).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


@register_handler("outbound", "discord_post")
async def discord_post(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """POST ``payload`` to ``row["url"]`` as a Discord webhook message."""
    url = row.get("url")
    if not url:
        raise ValueError("discord_post: row.url is required")

    body: dict[str, Any]
    if isinstance(payload, str):
        body = {"content": payload}
    elif isinstance(payload, dict):
        if "content" not in payload and "embeds" not in payload:
            raise ValueError("discord_post: payload needs 'content' or 'embeds'")
        body = payload
    else:
        raise TypeError(
            f"discord_post: payload must be str or dict, got {type(payload).__name__}"
        )

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body)

    # 204 No Content is Discord's success for webhook posts.
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"discord_post: HTTP {response.status_code}: {response.text[:200]}"
        )

    logger.debug(
        "[outbound.discord_post] delivered to %s (status=%s)",
        row.get("name"), response.status_code,
    )
    return {"status_code": response.status_code}
