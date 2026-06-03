"""
External webhook handlers — part of internal tracker Phase 3 Wave B.

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

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

external_webhooks_router = APIRouter(prefix="/api/webhooks", tags=["External Webhooks"])


# ---------------------------------------------------------------------------
# Lemon Squeezy
# ---------------------------------------------------------------------------


async def _verify_lemon_squeezy_signature(
    body: bytes, provided_signature: str | None, site_config: SiteConfig
) -> bool:
    """Lemon Squeezy signs payloads with HMAC-SHA256 using the webhook secret.

    The secret row is ``is_secret=true`` (encrypted with ``enc:v1:`` prefix);
    sync ``site_config.get`` returns the ciphertext, so we MUST use the async
    ``get_secret`` accessor to compare against plaintext. Same bug class as
    Glad-Labs/poindexter#325 — without this, every webhook silently fails
    signature verification and revenue events are dropped.

    See https://docs.lemonsqueezy.com/help/webhooks for the scheme.
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
        secret.encode("utf-8"), body, hashlib.sha256
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
    site_config: SiteConfig = Depends(get_site_config_dependency),
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


# Svix replay window. Per the Svix spec the default tolerance is 5 minutes on
# either side of the signed timestamp. Tunable per the config-in-DB discipline.
_RESEND_DEFAULT_TOLERANCE_SECONDS = 300


def _decode_svix_secret(secret: str) -> bytes | None:
    """Resolve a Svix/Resend ``whsec_<base64>`` signing secret to key bytes.

    Svix secrets are a base64-encoded key, conventionally prefixed with
    ``whsec_``. The HMAC key is the *decoded* payload bytes — keying HMAC
    with the literal ``whsec_...`` string was the #642 bug. Returns ``None``
    when the secret can't be base64-decoded so the caller fails closed.
    """
    raw = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    try:
        # binascii.Error (bad padding/alphabet) subclasses ValueError.
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        return None


async def _verify_resend_signature(
    body: bytes,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    site_config: SiteConfig,
    *,
    now: float | None = None,
) -> bool:
    """Verify a Resend webhook per the Svix signature spec.

    Resend signs webhooks with Svix. The scheme is::

        signed_content = f"{svix_id}.{svix_timestamp}.{body}"
        expected       = base64(HMAC_SHA256(base64decode(whsec), signed_content))

    The ``Svix-Signature`` header is a space-delimited list of ``v1,<base64sig>``
    entries (multiple during key rotation); a constant-time match against any
    entry passes. ``Svix-Timestamp`` outside the tolerance window is refused to
    thwart replay.

    The prior implementation HMAC'd only the body, keyed HMAC with the literal
    secret string, and compared hex — so it rejected every genuine Resend
    webhook (fail-closed → email events silently dropped) and had no replay
    protection (Glad-Labs/poindexter#642). ``resend_webhook_secret`` is
    ``is_secret=true``, so the plaintext MUST come from the async
    ``get_secret`` accessor (sync ``.get()`` returns ``enc:v1:`` ciphertext).

    https://docs.svix.com/receiving/verifying-payloads/how-manual
    """
    secret = await site_config.get_secret("resend_webhook_secret", "") or ""
    if not secret:
        logger.warning("[Resend] resend_webhook_secret not set — refusing webhook")
        return False
    if not (svix_id and svix_timestamp and svix_signature):
        logger.warning(
            "[Resend] missing Svix-Id / Svix-Timestamp / Svix-Signature header "
            "— refusing webhook",
        )
        return False

    # Replay protection: refuse timestamps outside the tolerance window.
    try:
        ts = int(svix_timestamp)
    except (TypeError, ValueError):
        logger.warning("[Resend] non-integer Svix-Timestamp — refusing webhook")
        return False
    try:
        tolerance = int(
            site_config.get(
                "resend_webhook_tolerance_seconds",
                str(_RESEND_DEFAULT_TOLERANCE_SECONDS),
            )
        )
    except (TypeError, ValueError):
        tolerance = _RESEND_DEFAULT_TOLERANCE_SECONDS
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance:
        logger.warning(
            "[Resend] Svix-Timestamp %s outside %ss tolerance — refusing (replay?)",
            ts, tolerance,
        )
        return False

    key = _decode_svix_secret(secret)
    if key is None:
        logger.warning(
            "[Resend] resend_webhook_secret is not valid base64 "
            "(expected whsec_<base64>) — refusing webhook",
        )
        return False

    signed_content = svix_id.encode("utf-8") + b"." + svix_timestamp.encode("utf-8") + b"." + body
    expected = base64.b64encode(
        hmac.new(key, signed_content, hashlib.sha256).digest()
    ).decode("utf-8")

    # Header: space-delimited "v1,<base64sig>" entries (Svix key rotation).
    for entry in svix_signature.split(" "):
        entry = entry.strip()
        if not entry:
            continue
        sig = entry.split(",", 1)[1] if "," in entry else entry
        if hmac.compare_digest(expected, sig):
            return True
    return False


@external_webhooks_router.post("/resend")
async def resend_webhook(
    request: Request,
    svix_id: str | None = Header(default=None, alias="Svix-Id"),
    svix_timestamp: str | None = Header(default=None, alias="Svix-Timestamp"),
    svix_signature: str | None = Header(default=None, alias="Svix-Signature"),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
):
    """Receive Resend webhooks and record a subscriber_events row.

    Supported event types: email.sent, email.delivered, email.opened,
    email.clicked, email.bounced, email.complained.

    Resend signs with Svix, sending ``Svix-Id`` / ``Svix-Timestamp`` /
    ``Svix-Signature`` — all three feed the signature check.
    """
    body = await request.body()
    if not await _verify_resend_signature(
        body, svix_id, svix_timestamp, svix_signature, site_config
    ):
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
