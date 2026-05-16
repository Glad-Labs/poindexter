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
   tests, CLI one-shots), fall back silently to the legacy direct
   Discord webhook path (``_legacy_discord_webhook`` below) so we
   never lose an alert to a framework wiring gap.
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

Prefect cutover Stage 4 (Glad-Labs/poindexter#410): the legacy
``services.task_executor._notify_discord`` was inlined here as
:func:`_legacy_discord_webhook` when ``task_executor.py`` was deleted.
The fallback path is unchanged — same secret key, same payload shape —
but now lives next to the framework path that wraps it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Lifespan-bound shared httpx.AsyncClient — main.py wires this via
# set_http_client() at startup. Fallback to a fresh per-call client
# below preserves behaviour for early boot / tests / CLI one-shots
# that run before the lifespan has fired.
http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient | None) -> None:
    """Wire the lifespan-bound shared httpx.AsyncClient."""
    global http_client
    http_client = client


async def _legacy_discord_webhook(message: str) -> None:
    """Direct-post fallback to the ``discord_ops_webhook_url`` secret row.

    Used when the integrations dispatcher framework is unavailable
    (early boot, tests, CLI one-shots) or returned an
    ``OutboundWebhookError`` (disabled / inbound-only row). The
    webhook URL is fetched via :func:`SiteConfig.get_secret` (async
    DB read) because ``is_secret=true`` rows are filtered out of the
    in-memory cache.

    Inlined from the deleted ``services.task_executor._notify_discord``
    helper. Best-effort: any failure logs at WARNING and returns.
    """
    site_config = _resolve_site_config()
    if site_config is None:
        logger.debug(
            "[NOTIFY:discord] No SiteConfig available — cannot resolve "
            "discord_ops_webhook_url; skipping legacy fallback"
        )
        return
    try:
        webhook_url = await site_config.get_secret("discord_ops_webhook_url", "")
        if not webhook_url:
            logger.debug(
                "[NOTIFY:discord] No discord_ops_webhook_url configured — skipping"
            )
            return
        # Prefer the lifespan-bound shared client; fall back to a
        # per-call client only when nothing has been wired yet (early
        # boot, tests, CLI one-shots before lifespan startup).
        logger.info("[NOTIFY:discord] %s", message[:80])
        if http_client is not None:
            await http_client.post(
                webhook_url, json={"content": message}, timeout=10.0,
            )
        else:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(webhook_url, json={"content": message})
    except Exception as exc:  # noqa: BLE001 — defensive: never raise
        logger.warning("[NOTIFY:discord] Failed: %s", exc)


def _resolve_site_config() -> Any | None:
    """Return the lifespan-bound SiteConfig if available, else None.

    The legacy fallback path needs a SiteConfig to call
    :meth:`get_secret`; we go through ``shared_context`` to avoid
    re-importing the module-level singleton (which was retired in
    glad-labs-stack#330).
    """
    try:
        from services.integrations.shared_context import get_site_config
        return get_site_config()
    except Exception:
        return None


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
    # The helper used to live in ``services.task_executor`` — that
    # module was deleted in the Prefect Stage 4 cutover
    # (Glad-Labs/poindexter#410). The fallback path was inlined here
    # so this module has zero coupling to the now-deleted dispatcher.
    try:
        await _legacy_discord_webhook(message)
    except Exception as e:
        logger.warning("[notify_operator] discord fallback failed: %s", e)
