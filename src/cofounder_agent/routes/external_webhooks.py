"""
External webhook handlers — part of gitea#271 Phase 3 Wave B.

Receives events from third-party services and mirrors them into the
feedback-loop tables:

  - POST /api/webhooks/lemon-squeezy → revenue_events
  - POST /api/webhooks/resend        → subscriber_events

Security: every handler verifies the signing secret pulled from
app_settings (``lemon_squeezy_webhook_secret`` / ``resend_webhook_secret``).
If the secret isn't set, the handler logs a warning and rejects so we
never silently accept unsigned events.

Registration (external — must be done once per environment):
    1. Lemon Squeezy: Store → Settings → Webhooks → Add endpoint
       URL: https://<your-domain>/api/webhooks/lemon-squeezy
       Events: order_created, order_refunded,
               subscription_created, subscription_updated, subscription_cancelled
       Copy the signing secret into ``app_settings.lemon_squeezy_webhook_secret``.

    2. Resend: https://resend.com/webhooks → Add Endpoint
       URL: https://<your-domain>/api/webhooks/resend
       Events: email.sent, email.delivered, email.opened,
               email.clicked, email.bounced, email.complained
       Copy the signing secret into ``app_settings.resend_webhook_secret``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

external_webhooks_router = APIRouter(prefix="/api/webhooks", tags=["External Webhooks"])


# ---------------------------------------------------------------------------
# Lemon Squeezy
# ---------------------------------------------------------------------------


async def _verify_lemon_squeezy_signature(
    body: bytes,
    provided_signature: str | None,
    site_config: Any,
) -> bool:
    """Lemon Squeezy signs payloads with HMAC-SHA256 using the webhook secret.

    See https://docs.lemonsqueezy.com/help/webhooks for the scheme.

    ``lemon_squeezy_webhook_secret`` is ``is_secret=true`` in app_settings,
    so we MUST go through ``site_config.get_secret(...)`` (async) to get
    the decrypted plaintext. A plain sync ``site_config.get(...)`` would
    return the ``enc:v1:<ciphertext>`` blob and the HMAC comparison would
    silently never match — the GH-107 secret-keys-audit failure mode.
    """
    secret = await site_config.get_secret("lemon_squeezy_webhook_secret", "") or ""
    if not secret:
        logger.warning(
            "[LemonSqueezy] lemon_squeezy_webhook_secret not set — refusing webhook",
        )
        return False
    if not provided_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, provided_signature)


_LS_EVENT_AMOUNT_KEYS = {
    # Lemon Squeezy order payloads use ``total`` in cents.
    "order_created": "total",
    "order_refunded": "total",
    # Subscription events carry subtotal/total on the object too.
    "subscription_created": "total",
    "subscription_updated": "total",
    "subscription_cancelled": "total",
}


@external_webhooks_router.post("/lemon-squeezy")
async def lemon_squeezy_webhook(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """Receive Lemon Squeezy webhooks and record a revenue_events row.

    Supported events: order_created, order_refunded, subscription_created /
    _updated / _cancelled. Everything else is recorded with ``amount_usd=0``
    for audit completeness.
    """
    body = await request.body()
    if not await _verify_lemon_squeezy_signature(body, x_signature, site_config):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    meta = payload.get("meta") or {}
    data = payload.get("data") or {}
    attrs = (data.get("attributes") or {})

    event_type = meta.get("event_name") or "unknown"
    amount_key = _LS_EVENT_AMOUNT_KEYS.get(event_type, "total")
    cents = attrs.get(amount_key) or 0
    try:
        amount_usd = float(cents) / 100.0
    except (TypeError, ValueError):
        amount_usd = 0.0
    # Refunds go in as negative.
    if event_type == "order_refunded":
        amount_usd = -abs(amount_usd)
    # Only order/subscription events count as recurring revenue.
    recurring = event_type.startswith("subscription_")
    customer_email = attrs.get("user_email")
    customer_id = str(data.get("id")) if data.get("id") is not None else None
    external_id = meta.get("webhook_id") or str(data.get("id"))

    if db_service.pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")
    try:
        await db_service.pool.execute(
            """
            INSERT INTO revenue_events (
                event_type, source, amount_usd, currency, recurring,
                customer_email, customer_id, external_id, external_data
            )
            VALUES ($1, 'lemon_squeezy', $2, 'USD', $3, $4, $5, $6, $7)
            """,
            event_type,
            amount_usd,
            recurring,
            customer_email,
            customer_id,
            external_id,
            json.dumps(payload),
        )
    except Exception as exc:
        logger.error(
            "[LemonSqueezy] revenue_events write failed: %s", exc, exc_info=True,
        )
        # 200 OK to prevent Lemon Squeezy retry storms when the DB is down —
        # the raw payload is preserved in external_data via the retry path
        # we set up elsewhere. For now, surface as 500 so Matt notices.
        raise HTTPException(status_code=500, detail="DB write failed") from exc

    logger.info(
        "[LemonSqueezy] Recorded %s ($%.2f, recurring=%s)",
        event_type, amount_usd, recurring,
    )
    return {"ok": True, "event": event_type}


# ---------------------------------------------------------------------------
# Resend (email events)
# ---------------------------------------------------------------------------


async def _verify_resend_signature(
    body: bytes,
    provided_signature: str | None,
    site_config: Any,
) -> bool:
    """Resend uses Svix-style HMAC signatures — Authorization-style header.

    https://resend.com/docs/dashboard/webhooks/verify-webhooks

    ``resend_webhook_secret`` is ``is_secret=true`` in app_settings, so we
    MUST use ``site_config.get_secret(...)`` (async) to receive the
    decrypted plaintext. The sync ``.get()`` returns ``enc:v1:...``
    ciphertext and would 401 every legitimate Resend delivery — same
    failure pattern documented in GH-107.
    """
    secret = await site_config.get_secret("resend_webhook_secret", "") or ""
    if not secret:
        logger.warning(
            "[Resend] resend_webhook_secret not set — refusing webhook",
        )
        return False
    if not provided_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    # Resend may format as "v1,<hex>" — split on comma and compare each.
    parts = [s.strip().split(",")[-1] for s in provided_signature.split(" ")]
    return any(hmac.compare_digest(expected, p) for p in parts)


@external_webhooks_router.post("/resend")
async def resend_webhook(
    request: Request,
    svix_signature: str | None = Header(default=None, alias="Svix-Signature"),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """Receive Resend webhooks and record a subscriber_events row.

    Supported event types: email.sent, email.delivered, email.opened,
    email.clicked, email.bounced, email.complained.
    """
    body = await request.body()
    if not await _verify_resend_signature(body, svix_signature, site_config):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event_type = payload.get("type") or "unknown"
    data = payload.get("data") or {}
    email = data.get("to") if isinstance(data.get("to"), str) else (
        (data.get("to") or [None])[0] if isinstance(data.get("to"), list) else None
    )

    if db_service.pool is None:
        raise HTTPException(status_code=503, detail="Database pool unavailable")
    try:
        await db_service.pool.execute(
            """
            INSERT INTO subscriber_events (email, event_type, event_data)
            VALUES ($1, $2, $3)
            """,
            email,
            event_type,
            json.dumps(payload),
        )
    except Exception as exc:
        logger.error("[Resend] subscriber_events write failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="DB write failed") from exc

    logger.info("[Resend] Recorded %s for %s", event_type, email)
    return {"ok": True, "event": event_type}
