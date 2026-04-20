"""Alertmanager webhook consumer.

Single endpoint — ``POST /api/webhooks/alertmanager`` — that consumes the
payload Alertmanager sends to its webhook receivers. Three responsibilities,
in order:

1. **Persist** every inbound alert to ``alert_events`` so the brain + operator
   UI + future audit queries have a historical record.
2. **Dispatch to the operator** (OpenClaw gateway → Telegram + Discord)
   for anything ``severity=critical`` or ``category=infrastructure``.
3. **Remediation scaffold** — look up ``plugin.remediation.<alertname>``
   in ``app_settings``. If present + enabled, hand off to a registry
   dispatcher. Phase D4 ships the hook; concrete remediation handlers
   arrive incrementally in follow-up commits.

Collapsing all three alertmanager routes (urgent / digest / brain) into
one endpoint — Phase-D ships sooner, and the dispatch logic is simpler
to reason about in code than spread across YAML routing trees. Brain
can read ``alert_events`` directly via DB since it shares the pool.

## Authentication

Bearer token via ``Authorization: Bearer <token>`` header. The token
is stored in ``app_settings.alertmanager_webhook_token`` (is_secret=true,
encrypted at rest). Alertmanager injects it via ``http_config.bearer_token``
in its routing config.

If the token row is empty OR missing, the endpoint rejects every
request with 503 — fail-closed, so a misconfigured install can't
silently accept unsigned webhooks. Tests override the dependency.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from services.logger_config import get_logger
from utils.route_utils import get_database_dependency

logger: logging.Logger = get_logger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["alertmanager"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def verify_alertmanager_token(
    authorization: str | None = Header(default=None),
    db: Any = Depends(get_database_dependency),
) -> None:
    """Reject the webhook unless the Authorization header carries the
    bearer token stored in ``app_settings.alertmanager_webhook_token``.

    Fail-closed semantics:
    - Missing header -> 401
    - Malformed header (no ``Bearer `` prefix) -> 401
    - Empty or unset token in app_settings -> 503 (server misconfigured)
    - Token mismatch -> 401

    The token is stored ``is_secret=true`` so it's encrypted at rest;
    ``plugins.secrets.get_secret`` transparently decrypts. ``hmac.compare_digest``
    is used for the comparison to avoid timing side channels.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="alertmanager webhook requires Bearer token",
        )
    submitted = authorization[len("Bearer "):].strip()

    from plugins.secrets import get_secret
    async with db.pool.acquire() as conn:
        expected = await get_secret(conn, "alertmanager_webhook_token")

    if not expected:
        logger.error(
            "alertmanager webhook: alertmanager_webhook_token is unset; "
            "rejecting all inbound webhooks until configured"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook auth not configured",
        )

    if not hmac.compare_digest(submitted, expected):
        logger.warning("alertmanager webhook: token mismatch")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid Bearer token",
        )


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


_ENSURE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS alert_events (
    id BIGSERIAL PRIMARY KEY,
    alertname TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'firing' | 'resolved'
    severity TEXT,
    category TEXT,
    labels JSONB NOT NULL DEFAULT '{}'::jsonb,
    annotations JSONB NOT NULL DEFAULT '{}'::jsonb,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    fingerprint TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_events_received_at
  ON alert_events (received_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_events_alertname
  ON alert_events (alertname, received_at DESC);
"""


async def _ensure_table(pool: Any) -> None:
    """Idempotent schema creation — cheap to call on every request."""
    async with pool.acquire() as conn:
        await conn.execute(_ENSURE_TABLE_SQL)


async def _insert_alert(pool: Any, alert: dict[str, Any]) -> None:
    """Persist one alert from the Alertmanager payload."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_events
              (alertname, status, severity, category, labels, annotations,
               starts_at, ends_at, fingerprint)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb,
                    $7::timestamptz, $8::timestamptz, $9)
            """,
            labels.get("alertname", "unknown"),
            alert.get("status", "firing"),
            labels.get("severity"),
            labels.get("category"),
            json.dumps(labels),
            json.dumps(annotations),
            alert.get("startsAt"),
            alert.get("endsAt"),
            alert.get("fingerprint"),
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _should_page_operator(alert: dict[str, Any]) -> bool:
    """True when a human should see this in Telegram/Discord immediately."""
    if alert.get("status") == "resolved":
        return False
    labels = alert.get("labels") or {}
    severity = (labels.get("severity") or "").lower()
    category = (labels.get("category") or "").lower()
    if severity == "critical":
        return True
    if category == "infrastructure":
        return True
    return False


def _format_alert_message(alert: dict[str, Any]) -> str:
    """Render a compact, human-readable Telegram/Discord message."""
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}
    alertname = labels.get("alertname", "UnknownAlert")
    severity = labels.get("severity") or "info"
    status = alert.get("status", "firing").upper()
    summary = annotations.get("summary", "")
    description = (annotations.get("description") or "").strip()

    header = f"[{status} · {severity}] {alertname}"
    if summary:
        header = f"{header} — {summary}"
    if description:
        return f"{header}\n\n{description}"
    return header


async def _dispatch_to_operator(alert: dict[str, Any]) -> None:
    """Send the alert to the OpenClaw gateway.

    Uses the existing ``_notify_openclaw`` helper — OpenClaw owns the
    Telegram + Discord bot tokens, the worker just POSTs a message.
    Critical severity gets the ``critical=True`` flag so OpenClaw routes
    to the high-urgency channel.
    """
    try:
        from services.task_executor import _notify_openclaw
    except Exception as e:
        logger.warning("alertmanager webhook: _notify_openclaw unavailable: %s", e)
        return

    severity = (alert.get("labels") or {}).get("severity", "info").lower()
    message = _format_alert_message(alert)
    try:
        await _notify_openclaw(message, critical=severity == "critical")
    except Exception as e:
        logger.warning("alertmanager webhook: operator dispatch failed: %s", e)


# ---------------------------------------------------------------------------
# Remediation scaffold
# ---------------------------------------------------------------------------


async def _maybe_remediate(pool: Any, alert: dict[str, Any]) -> str | None:
    """Look up ``plugin.remediation.<alertname>`` and invoke if configured.

    Returns a short status string (logged), or None if nothing ran.
    The registry is intentionally open-ended: handlers register via
    entry_points in a later phase. For now the scaffold just records
    that the hook fired and logs the intended action.
    """
    if alert.get("status") == "resolved":
        return None
    alertname = (alert.get("labels") or {}).get("alertname")
    if not alertname:
        return None

    key = f"plugin.remediation.{alertname}"
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
    if not raw:
        return None
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("remediation: %s has malformed JSON; skipping", key)
        return None
    if not spec.get("enabled"):
        return None
    action = spec.get("action") or "unknown"
    # Phase D ships only the scaffold; concrete handlers land in follow-ups.
    logger.info(
        "remediation: %s would run action=%r params=%r (not yet implemented)",
        alertname, action, spec.get("params"),
    )
    return f"scheduled remediation action={action}"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/alertmanager", dependencies=[Depends(verify_alertmanager_token)])
async def alertmanager_webhook(
    payload: dict[str, Any],
    db: Any = Depends(get_database_dependency),
) -> dict[str, Any]:
    """Consume an Alertmanager webhook payload.

    The payload shape is Alertmanager v2:
    ``{status, alerts: [{status, labels, annotations, startsAt, endsAt, fingerprint}]}``.
    We treat the top-level ``status`` as informational only — each alert
    carries its own ``status`` that we persist.
    """
    alerts = payload.get("alerts") or []
    if not isinstance(alerts, list):
        logger.warning("alertmanager webhook: 'alerts' was not a list: %r", type(alerts))
        return {"ok": False, "reason": "alerts must be a list", "count": 0}

    pool = db.pool
    try:
        await _ensure_table(pool)
    except Exception as e:
        logger.exception("alertmanager webhook: ensure_table failed: %s", e)

    persisted = 0
    paged = 0
    remediated = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            continue

        try:
            await _insert_alert(pool, alert)
            persisted += 1
        except Exception as e:
            logger.warning("alertmanager webhook: insert failed: %s", e)

        if _should_page_operator(alert):
            await _dispatch_to_operator(alert)
            paged += 1

        try:
            rem = await _maybe_remediate(pool, alert)
            if rem:
                remediated += 1
        except Exception as e:
            logger.warning("alertmanager webhook: remediation lookup failed: %s", e)

    logger.info(
        "alertmanager webhook: received=%d persisted=%d paged=%d remediated=%d",
        len(alerts), persisted, paged, remediated,
    )
    return {
        "ok": True,
        "count": len(alerts),
        "persisted": persisted,
        "paged": paged,
        "remediated": remediated,
    }
