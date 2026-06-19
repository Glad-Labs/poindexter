"""Handler: ``outbound.apprise_notify`` — generic notification delivery.

One data-driven handler that replaces the per-channel ``outbound_discord``
and ``outbound_telegram`` notify handlers. The destination is described by
the row's ``config.apprise_url`` template; adding a new channel is a row
insert, not a new module.

Template substitution (``config.apprise_url``):

- ``{secret}``  -> resolved via ``secret_key_ref`` (see secret_resolver)
- ``{<token>}`` -> the row's ``config[<token>]`` when set and non-empty,
                   else the operator setting ``app_settings.<token>``
                   (resolved through ``site_config`` — the DB-first single
                   source of truth). Empty/unset on BOTH paths fails loud
                   rather than emitting a blank URL segment (no send to a
                   blank destination — feedback_no_silent_defaults). Only
                   NON-secret settings are reachable this way: ``SiteConfig``
                   filters ``is_secret`` rows out of its cache, so a
                   placeholder can never exfiltrate a secret — secrets use
                   ``{secret}`` + ``secret_key_ref``.

Examples:

- telegram_ops : ``tgram://{secret}/{telegram_chat_id}/``
                 + ``secret_key_ref=telegram_bot_token``. The chat_id
                 resolves from ``app_settings.telegram_chat_id`` — the seeded
                 row carries NO operator identity (symmetric with discord_ops).
- discord_ops  : ``{secret}`` + ``secret_key_ref=discord_ops_webhook_url``
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
    template: str,
    secret: str | None,
    config: dict[str, Any],
    site_config: Any,
    row_name: Any,
) -> str:
    """Substitute ``{secret}`` + ``{token}`` placeholders in the template.

    Resolution order for a ``{token}`` (other than ``{secret}``):

    1. a non-empty ``config[token]`` — explicit per-row override
    2. a non-empty ``site_config.get(token)`` — operator ``app_settings``
       (DB-first single source of truth, e.g. ``{telegram_chat_id}``)
    3. otherwise fail loud — never emit a blank segment / send to a blank
       destination (feedback_no_silent_defaults)
    """

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
        # Per-row config override wins when present and non-empty.
        cfg_val = config.get(token)
        if cfg_val is not None and str(cfg_val).strip():
            return str(cfg_val)
        # Fall back to the operator's app_settings value (single source of
        # truth). SiteConfig.get reads the non-secret cache, so this can never
        # resolve an is_secret key — secrets must use {secret}.
        if site_config is not None and hasattr(site_config, "get"):
            setting_val = site_config.get(token, "")
            if setting_val and str(setting_val).strip():
                return str(setting_val)
        raise RuntimeError(
            f"apprise_notify: row {row_name!r} apprise_url references "
            f"{{{token}}} but it resolved empty — set app_settings.{token} "
            f"(or row config.{token}) so the notification has a destination"
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
    url = _build_url(str(template), secret, config, site_config, row_name)

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
