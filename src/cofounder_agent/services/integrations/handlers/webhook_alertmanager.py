"""Handler: ``webhook.alertmanager_dispatch``.

Consumes an Alertmanager webhook payload, persists every alert to
``alert_events``, runs the should-page filter (severity=critical OR
category=infrastructure), fans matching alerts out to Discord +
Telegram via :func:`services.task_executor._notify_alert`, and fires
the remediation scaffold.

Migrated from ``routes/alertmanager_webhook_routes.py``. The dispatch
logic and the remediation scaffold move wholesale; only the transport
changes from a bespoke FastAPI route to the declarative dispatcher.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

from services.integrations.registry import register_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timestamp parsing (Alertmanager ISO-8601 → datetime)
# ---------------------------------------------------------------------------


def _parse_iso(val: Any) -> Any:
    if not val:
        return None
    if hasattr(val, "isoformat"):
        return val
    if isinstance(val, str):
        if val.startswith("0001-01-01"):  # "never ended" sentinel
            return None
        s = val.rstrip("Z")
        if not any(c in s[10:] for c in ("+", "-")):
            s = s + "+00:00"
        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------


async def _insert_alert(pool: Any, alert: dict[str, Any]) -> None:
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
            _parse_iso(alert.get("startsAt")),
            _parse_iso(alert.get("endsAt")),
            alert.get("fingerprint"),
        )


# ---------------------------------------------------------------------------
# Dispatch decision + message formatting
# ---------------------------------------------------------------------------


def _should_page_operator(alert: dict[str, Any]) -> bool:
    if alert.get("status") == "resolved":
        return False
    labels = alert.get("labels") or {}
    severity = (labels.get("severity") or "").lower()
    category = (labels.get("category") or "").lower()
    return severity == "critical" or category == "infrastructure"


def _format_alert_message(alert: dict[str, Any]) -> str:
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


async def _notify_operator(alert: dict[str, Any], site_config: Any) -> None:
    try:
        from services.task_executor import _notify_alert
    except Exception as exc:
        logger.warning("alertmanager_dispatch: _notify_alert unavailable: %s", exc)
        return

    severity = (alert.get("labels") or {}).get("severity", "info").lower()
    message = _format_alert_message(alert)
    try:
        await _notify_alert(message, site_config, critical=severity == "critical")
    except Exception as exc:
        logger.warning("alertmanager_dispatch: operator dispatch failed: %s", exc)


# ---------------------------------------------------------------------------
# Remediation scaffold
# ---------------------------------------------------------------------------


async def _maybe_remediate(pool: Any, alert: dict[str, Any]) -> str | None:
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
        logger.warning("remediation: %s malformed JSON; skipping", key)
        return None
    if not spec.get("enabled"):
        return None
    action = spec.get("action") or "unknown"
    logger.info(
        "remediation: %s would run action=%r params=%r (not yet implemented)",
        alertname, action, spec.get("params"),
    )
    return f"scheduled remediation action={action}"


# ---------------------------------------------------------------------------
# Handler entry point
# ---------------------------------------------------------------------------


@register_handler("webhook", "alertmanager_dispatch")
async def alertmanager_dispatch(
    payload: Any,
    *,
    site_config: Any,
    row: dict[str, Any],
    pool: Any,
) -> dict[str, Any]:
    """Process a batch of alerts from one Alertmanager webhook call."""
    if not isinstance(payload, dict):
        return {"persisted": 0, "paged": 0, "remediated": 0}
    if pool is None:
        raise RuntimeError("database pool unavailable")

    alerts = payload.get("alerts") or []
    persisted = paged = remediated = 0

    for alert in alerts:
        try:
            await _insert_alert(pool, alert)
            persisted += 1
        except Exception:
            logger.exception("alertmanager_dispatch: insert failed")

        if _should_page_operator(alert):
            await _notify_operator(alert, site_config)
            paged += 1

        try:
            status = await _maybe_remediate(pool, alert)
            if status:
                remediated += 1
        except Exception:
            logger.exception("alertmanager_dispatch: remediation lookup failed")

    return {
        "count": len(alerts),
        "persisted": persisted,
        "paged": paged,
        "remediated": remediated,
    }
