"""Telegram Bot API helpers (``sendMessage`` / ``editMessageText``).

These low-level helpers back the pipeline edit-streaming path
(``services/pipeline_streaming.py``), which sends a message and then
edits it in place as a run progresses — behaviour Apprise's
fire-and-forget model cannot express. Operator notifications now go
through the generic ``outbound.apprise_notify`` handler, so this module
no longer registers a dispatcher handler; it is a helper library only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
