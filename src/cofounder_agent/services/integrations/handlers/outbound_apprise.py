"""Handler: ``outbound.apprise_notify`` — generic notification delivery.

One data-driven handler that replaces the per-channel ``outbound_discord``
and ``outbound_telegram`` notify handlers. The destination is described by
the row's ``config.apprise_url`` template; adding a new channel is a row
insert, not a new module.

Template substitution (``config.apprise_url``):

- ``{secret}``       -> resolved via ``secret_key_ref`` (see secret_resolver)
- ``{<config-key>}`` -> any other key in the row's ``config`` (e.g. ``{chat_id}``)

Examples:

- telegram_ops : ``tgram://{secret}/{chat_id}/`` + ``secret_key_ref=telegram_bot_token``
- discord_ops  : ``{secret}``                    + ``secret_key_ref=discord_ops_webhook_url``
  (Apprise accepts the native ``https://discord.com/api/webhooks/ID/TOKEN`` URL directly.)

Payload: a plain ``str`` or a dict carrying one of
``content`` / ``text`` / ``body`` / ``message`` (so existing callers and the
old Discord/Telegram payload shapes keep working unchanged).

Apprise is synchronous internally, so delivery is offloaded with
``asyncio.to_thread`` to keep the event loop free. Any failure raises — the
outbound dispatcher records it on the row and re-raises.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import apprise

from services.integrations.registry import register_handler
from services.integrations.secret_resolver import resolve_secret
from services.logger_config import get_logger

logger = get_logger(__name__)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_BODY_KEYS = ("content", "text", "body", "message")


def _coerce_body(payload: Any) -> str:
    """Reduce the supported payload shapes to a notification body string."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _BODY_KEYS:
            value = payload.get(key)
            if value:
                return str(value)
        raise TypeError(
            "apprise_notify: dict payload needs one of "
            f"{_BODY_KEYS!r} with a non-empty value"
        )
    raise TypeError(
        f"apprise_notify: payload must be str or dict, got {type(payload).__name__}"
    )


def _build_url(
    template: str, secret: str | None, config: dict[str, Any], row_name: Any
) -> str:
    """Substitute ``{secret}`` + ``{config-key}`` placeholders in the template."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "secret":
            if not secret:
                raise RuntimeError(
                    f"apprise_notify: row {row_name!r} apprise_url references "
                    "{secret} but no secret resolved — check secret_key_ref and "
                    "the referenced app_settings key"
                )
            return secret
        if token in config:
            return str(config[token])
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} apprise_url references "
            f"{{{token}}} but row config has no such key"
        )

    return _PLACEHOLDER.sub(_replace, template)


@register_handler("outbound", "apprise_notify")
async def apprise_notify(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,  # noqa: ARG001 — handler protocol signature; pool unused here
) -> dict[str, Any]:
    """Deliver ``payload`` to the destination described by ``row.config.apprise_url``."""
    row_name = row.get("name")
    config = row.get("config") or {}
    if not isinstance(config, dict):
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} config is not an object"
        )
    template = config.get("apprise_url")
    if not template:
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} config.apprise_url is required"
        )

    body = _coerce_body(payload)
    secret = await resolve_secret(row, site_config)
    url = _build_url(str(template), secret, config, row_name)

    aobj = apprise.Apprise()
    if not aobj.add(url):
        raise RuntimeError(
            f"apprise_notify: Apprise rejected the URL for row {row_name!r} "
            "(malformed apprise_url?)"
        )

    # Apprise's notify() is blocking (requests-based); offload it.
    delivered = await asyncio.to_thread(aobj.notify, body=body, title="")
    if not delivered:
        raise RuntimeError(
            f"apprise_notify: delivery failed for row {row_name!r}"
        )

    logger.debug("[outbound.apprise_notify] delivered via row %s", row_name)
    return {"delivered": True}
