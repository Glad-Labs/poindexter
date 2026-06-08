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
    pool: Any,  # noqa: ARG001 — handler protocol signature; pool unused by telegram_post
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

    result = await send_telegram_message(base_url, bot_token, chat_id, text)

    logger.debug(
        "[outbound.telegram_post] delivered to chat %s (row=%s, message_id=%s)",
        chat_id, row.get("name"), result.get("message_id"),
    )
    return {
        "status_code": 200,
        "chat_id": str(chat_id),
        "message_id": result.get("message_id"),
    }


# ---------------------------------------------------------------------------
# Low-level Bot API helpers (Glad-Labs/poindexter#361 part 2)
#
# The handler above DISCARDED the returned message_id, so there was no way to
# edit a message in place. The pipeline edit-streaming channel
# (services/pipeline_streaming.py) needs both (a) the message_id from the
# initial sendMessage and (b) an editMessageText call to update that single
# message as the run progresses (instead of spamming N messages). These two
# helpers expose exactly that, reused by the handler above + the streaming
# callback.
# ---------------------------------------------------------------------------


async def send_telegram_message(
    base_url: str,
    bot_token: str,
    chat_id: Any,
    text: str,
) -> dict[str, Any]:
    """POST ``sendMessage`` and return the parsed Bot API ``result`` dict.

    The ``result`` includes ``message_id``, which the caller stores so it can
    later ``edit_telegram_message`` the same message in place. Raises
    ``RuntimeError`` on non-200 HTTP or a Bot API ``ok: false`` envelope.
    """
    url = f"{base_url.rstrip('/')}/bot{bot_token}/sendMessage"
    body = {"chat_id": chat_id, "text": text}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body)
    return _parse_bot_result(response, "sendMessage")


async def edit_telegram_message(
    base_url: str,
    bot_token: str,
    chat_id: Any,
    message_id: Any,
    text: str,
) -> dict[str, Any]:
    """POST ``editMessageText`` to update an existing message in place.

    Returns the parsed Bot API ``result``. Raises ``RuntimeError`` on
    non-200 HTTP or a Bot API ``ok: false`` envelope (e.g. the
    "message is not modified" error when the text is unchanged — callers
    that coalesce edits should avoid re-sending identical text).
    """
    url = f"{base_url.rstrip('/')}/bot{bot_token}/editMessageText"
    body = {"chat_id": chat_id, "message_id": message_id, "text": text}
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body)
    return _parse_bot_result(response, "editMessageText")


def _parse_bot_result(response: Any, method: str) -> dict[str, Any]:
    """Validate a Bot API response + return its ``result`` dict.

    Telegram wraps successes as ``{"ok": true, "result": {...}}`` and
    failures as ``{"ok": false, "description": "..."}``. On a non-200 status,
    raise. On a 200 with a parseable ``ok: false`` envelope, raise (a real
    Bot API rejection). On a 200 with an unparseable body, treat it as
    success with an empty ``result`` — the body isn't JSON we can extract a
    message_id from, but the HTTP call succeeded; the streaming path simply
    gets no message_id (and thus skips editing). This keeps the handler
    tolerant of mocks / proxies that don't echo a JSON envelope.
    """
    if response.status_code != 200:
        raise RuntimeError(
            f"telegram {method}: HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )
    try:
        envelope = response.json()
    except Exception:  # noqa: BLE001 — non-JSON 200: tolerate, no result
        return {}
    if isinstance(envelope, dict) and envelope.get("ok") is False:
        raise RuntimeError(
            f"telegram {method}: Bot API error: {envelope.get('description')}"
        )
    if isinstance(envelope, dict):
        result = envelope.get("result")
        return result if isinstance(result, dict) else {}
    return {}
