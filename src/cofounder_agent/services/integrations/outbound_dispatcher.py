"""Outbound webhook / notification dispatcher.

Counterpart to :mod:`webhook_dispatcher`. Where the inbound side serves
a FastAPI route driven by external callers, the outbound side is
called from inside the application whenever an internal event needs
to reach a third-party service (Discord notification, Telegram ping,
Vercel ISR revalidation, future outbound webhooks added by operators).

Contract:

- Callers invoke :func:`deliver` with ``name`` (the row slug) and a
  handler-specific payload dict.
- :func:`deliver` loads the row, verifies it's outbound + enabled,
  and calls the registered handler.
- The handler is responsible for the HTTP/API mechanics — POST shape,
  multipart encoding, auth header format, etc. Different destinations
  need different protocols (Discord webhooks vs Telegram Bot API vs
  signed Vercel POST), and pushing that variation into handlers keeps
  the dispatcher simple.
- Handler exceptions surface. The dispatcher records them on the row
  and re-raises so callers can decide whether to retry.

This is the in-process delivery path. For durable outbound delivery
with retries (the ``webhook_events`` queue pattern), see
``services/webhook_delivery_service.py`` which pre-dates this
framework and will migrate onto it in a follow-up.
"""

from __future__ import annotations

import logging
from typing import Any

from services.integrations import registry

logger = logging.getLogger(__name__)


class OutboundWebhookError(RuntimeError):
    """Raised when delivery fails — includes the row name for context."""


async def deliver(
    name: str,
    payload: Any,
    *,
    db_service: Any,
    site_config: Any,
) -> dict[str, Any]:
    """Dispatch an outbound webhook/notification by row name.

    Args:
        name: The ``webhook_endpoints.name`` slug of the destination.
        payload: Handler-specific dict (e.g. ``{"content": "..."}`` for
            Discord, ``{"text": "..."}`` for Telegram,
            ``{"paths": [...], "tags": [...]}`` for Vercel ISR).
        db_service: DatabaseService instance (for row lookup and counter
            updates). Must have an initialized pool.
        site_config: SiteConfig for secret resolution.

    Returns:
        The handler's result dict, augmented with ``{"ok": True, "name": <name>}``.

    Raises:
        :class:`OutboundWebhookError` if the row is missing, disabled,
        or inbound-only.
        Whatever the handler raises — the dispatcher re-raises so the
        caller can decide to retry or surface to the operator.
    """
    row = await _load_row(db_service, name)
    if row is None:
        raise OutboundWebhookError(f"unknown outbound webhook: {name}")
    if not row["enabled"]:
        raise OutboundWebhookError(f"outbound webhook disabled: {name}")
    if row["direction"] != "outbound":
        raise OutboundWebhookError(
            f"webhook is inbound, not callable outbound: {name}"
        )

    try:
        result = await registry.dispatch(
            "outbound",
            row["handler_name"],
            payload,
            site_config=site_config,
            row=dict(row),
            pool=db_service.pool,
        )
    except registry.HandlerRegistrationError:
        await _record_failure(
            db_service, row["id"], f"unknown handler: {row['handler_name']}"
        )
        raise
    except Exception as exc:
        logger.exception(
            "[outbound-dispatch] handler %r raised for %s",
            row["handler_name"], name,
        )
        await _record_failure(db_service, row["id"], f"handler exception: {exc}")
        raise

    await _record_success(db_service, row["id"])

    out: dict[str, Any] = {"ok": True, "name": name}
    if isinstance(result, dict):
        out.update(result)
    return out


# ---------------------------------------------------------------------------
# Row + counter helpers (mirrors inbound dispatcher shape)
# ---------------------------------------------------------------------------


async def _load_row(db_service: Any, name: str) -> dict[str, Any] | None:
    if db_service.pool is None:
        raise OutboundWebhookError("database pool unavailable")
    row = await db_service.pool.fetchrow(
        """
        SELECT id, name, direction, handler_name, path, url, signing_algorithm,
               secret_key_ref, event_filter, enabled, config, metadata
          FROM webhook_endpoints
         WHERE name = $1
        """,
        name,
    )
    return dict(row) if row else None


async def _record_success(db_service: Any, row_id: Any) -> None:
    if db_service.pool is None:
        return
    await db_service.pool.execute(
        """
        UPDATE webhook_endpoints
           SET last_success_at = now(),
               total_success = total_success + 1,
               last_error = NULL
         WHERE id = $1
        """,
        row_id,
    )


async def _record_failure(db_service: Any, row_id: Any, error: str) -> None:
    if db_service.pool is None:
        return
    await db_service.pool.execute(
        """
        UPDATE webhook_endpoints
           SET last_failure_at = now(),
               total_failure = total_failure + 1,
               last_error = $2
         WHERE id = $1
        """,
        row_id,
        error,
    )
