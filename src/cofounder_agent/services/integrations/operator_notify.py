"""Operator notification helper — thin wrapper over ``outbound_dispatcher.deliver``.

Replaces the legacy ``services.task_executor._notify_openclaw`` helper
with a path that goes directly to the ``webhook_endpoints`` rows
(``discord_ops`` for routine notifications, ``telegram_ops`` for
critical pages).

Why this lives in :mod:`services.integrations`:

The dispatcher framework owns row lookup + secret resolution, so the
operator-notify shim is the framework's natural extension. Call sites
import one symbol (``notify_operator``) and never have to thread the
DB pool, site_config, or row name through their call signature.

Resolution path:

1. Get the process-wide ``DatabaseService`` from
   :mod:`services.integrations.shared_context`. If unset (early boot,
   tests, CLI one-shots), fall back silently to the legacy
   ``_notify_discord`` direct webhook path so we never lose an alert
   to a framework wiring gap.
2. Get the active ``SiteConfig`` from the module-level singleton.
3. Call :func:`outbound_dispatcher.deliver` with name=``discord_ops``
   for ``critical=False`` notifications, name=``telegram_ops`` for
   ``critical=True`` (which historically fanned out to both via
   OpenClaw — Telegram is the high-urgency channel Matt sees on his
   phone, Discord is the durable record).
4. Best-effort: any exception is swallowed and logged. Operator
   notifications must never take down the calling code path.

This intentionally has zero new configuration — the row enable/disable
is done in the DB by the operator. Disabled rows raise
:class:`OutboundWebhookError` from inside the dispatcher; we treat
that as "operator chose not to receive this" and move on quietly.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def notify_operator(
    message: str,
    *,
    critical: bool = False,
    site_config: Any = None,
) -> None:
    """Send an operator notification via the outbound dispatcher.

    Args:
        message: Plain-text message body. Discord renders Markdown;
            Telegram treats it as plain text. Keep formatting simple.
        critical: When ``True``, route via the ``telegram_ops`` row so
            the message hits the operator's phone with a push
            notification. When ``False``, route via ``discord_ops``
            (durable record, no push).
        site_config: SiteConfig DI seam (glad-labs-stack#330) — passed
            through to outbound_dispatcher.deliver. Optional because
            many notify_operator callers don't have one in scope (e.g.
            module-level startup checks). The dispatcher's secret
            decryption silently no-ops on a missing config; the
            notification still attempts the delivery.

    Never raises — operator notifications are best-effort.
    """
    # Phase 1: framework path.
    try:
        from services.integrations import outbound_dispatcher
        from services.integrations.shared_context import get_database_service

        db_service = get_database_service()
        if db_service is not None and getattr(db_service, "pool", None) is not None:
            row_name = "telegram_ops" if critical else "discord_ops"
            payload: dict[str, Any] = (
                {"text": message} if critical else {"content": message}
            )
            try:
                await outbound_dispatcher.deliver(
                    row_name,
                    payload,
                    db_service=db_service,
                    site_config=site_config,
                )
                return
            except outbound_dispatcher.OutboundWebhookError as e:
                # Row missing / disabled / inbound-only — operator turned
                # the channel off, or migration 0086 hasn't run. Fall
                # through to the legacy Discord path so we still get
                # *some* notification on the dev box.
                logger.debug(
                    "[notify_operator] dispatcher rejected %s: %s — falling back",
                    row_name, e,
                )
            except Exception as e:
                # Handler raised (network, auth, etc). Swallow + log;
                # the dispatcher already recorded the failure on the row
                # so Grafana can surface it.
                logger.warning(
                    "[notify_operator] dispatcher %s failed: %s",
                    row_name, e,
                )
                # Critical messages still try the Discord fallback so an
                # outbound Telegram outage doesn't drop the page entirely.
                if not critical:
                    return
    except Exception as e:
        # Framework not importable / shared_context missing. Don't
        # spam the warning log — this is the expected path during
        # bootstrap and in many unit tests.
        logger.debug("[notify_operator] framework path unavailable: %s", e)

    # Phase 2: legacy Discord webhook fallback. Always best-effort.
    try:
        from services.task_executor import _notify_discord
        await _notify_discord(message)
    except Exception as e:
        logger.warning("[notify_operator] discord fallback failed: %s", e)
