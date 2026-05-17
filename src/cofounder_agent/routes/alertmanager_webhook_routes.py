"""Alertmanager webhook consumer — persistence-only sink.

Single endpoint — ``POST /api/webhooks/alertmanager`` — that consumes
the payload Alertmanager / Grafana sends to its webhook receivers.
Two responsibilities, in order:

1. **Persist** every inbound alert to ``alert_events`` so the brain
   daemon (the dispatcher), operator UI, and future audit queries have
   a historical record. Rows land with ``dispatched_at IS NULL`` and
   the brain's ``alert_dispatcher`` poll picks them up on its 30s
   cadence (see ``brain/alert_dispatcher.py``).
2. **Remediation scaffold** — look up ``plugin.remediation.<alertname>``
   in ``app_settings``. If present + enabled, hand off to a registry
   dispatcher. Phase D4 ships the hook; concrete remediation handlers
   arrive incrementally in follow-up commits.

The webhook used to ALSO fan out to Telegram/Discord inline via
``services.integrations.operator_notify.notify_operator``. That coupled
the operator's pager to the worker's uptime — when the worker crashed
mid-alert the page silently disappeared. Dispatch was moved into the
brain daemon (Glad-Labs/poindexter#340 prep) so:

- Source-of-truth row exists before any dispatch is attempted.
- Brain re-attempts on restart by polling ``WHERE dispatched_at IS NULL``.
- Future LLM-triage step (Phase A in #340) drops in by replacing the
  dispatcher's "always page" body — the poll/mark plumbing stays.

So this endpoint is now: *receive → persist → optionally remediate*.
All operator-facing dispatch (Telegram + Discord) happens in the
brain daemon.

Collapsing all three alertmanager routes (urgent / digest / brain) into
one endpoint — Phase-D ships sooner, and the dispatch logic is simpler
to reason about in code than spread across YAML routing trees. Brain
reads ``alert_events`` directly via DB since it shares the pool.

## Authentication

Bearer token via ``Authorization: Bearer <token>`` header. Two token
shapes are accepted (Glad-Labs/poindexter#247):

1. **OAuth JWT** issued by ``services.auth.oauth_issuer`` — typically a
   long-TTL token (e.g. 90 days) minted via
   ``poindexter auth mint-grafana-token``. Verified by signature +
   expiry + issuer. Pasted into the Grafana contact-point UI by the
   operator (Grafana's contact-point OAuth flow is fragile, so a
   pre-issued long-TTL JWT — option B in #247 — is the ergonomic
   choice).
2. **Static Bearer** — the legacy ``app_settings.alertmanager_webhook_token``.
   Kept for the Phase 2 migration window so existing Alertmanager
   configs keep working until Phase 3 (#249) retires it.

Dispatch order: tokens with three dot-separated segments go through
JWT verify first; everything else falls through to the static path.
If neither path is configured AND the submitted token isn't a valid
JWT, the endpoint rejects with 503 — fail-closed, so a misconfigured
install can't silently accept unsigned webhooks. Tests override the
dependency.
"""

from __future__ import annotations

import datetime as _dt
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


def _looks_like_jwt(token: str) -> bool:
    """Cheap shape check — three non-empty base64url segments joined by '.'.

    Lets us skip the JWT verify path for legacy 32-char static tokens
    without paying for an inevitable failed signature check.
    """
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


async def verify_alertmanager_token(
    authorization: str | None = Header(default=None),
    db: Any = Depends(get_database_dependency),
) -> None:
    """Reject the webhook unless the Authorization header carries either
    a valid OAuth JWT (Glad-Labs/poindexter#247) or the legacy bearer
    token stored in ``app_settings.alertmanager_webhook_token``.

    Dispatch:
    - Token shaped like a JWT (three dot segments) → verify via
      ``services.auth.oauth_issuer.verify_token``. Pass = 200.
    - Token doesn't look like a JWT, OR JWT verification raises
      ``InvalidToken`` → fall through to the static-Bearer path.
    - Neither path configured AND the JWT path didn't accept → 503
      (fail-closed, server misconfigured).
    - Static token mismatch → 401.

    The static token is stored ``is_secret=true`` so it's encrypted at
    rest; ``plugins.secrets.get_secret`` transparently decrypts.
    ``hmac.compare_digest`` is used for the static comparison to avoid
    timing side channels.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="alertmanager webhook requires Bearer token",
        )
    submitted = authorization[len("Bearer "):].strip()

    # OAuth JWT path. Tokens minted via `poindexter auth mint-grafana-token`
    # land here. We verify but don't enforce a particular client_id —
    # any valid JWT issued by this Poindexter is accepted, since the
    # operator is the one wiring it into Grafana's contact-point config.
    # Scope enforcement could be added later if more clients start
    # speaking to this endpoint.
    if _looks_like_jwt(submitted):
        try:
            from services.auth.oauth_issuer import verify_token, InvalidToken
            try:
                verify_token(submitted)
                return  # OAuth JWT accepted.
            except InvalidToken as e:
                logger.warning("alertmanager webhook: OAuth JWT rejected: %s", e)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"invalid_token: {e}",
                    headers={
                        "WWW-Authenticate": 'Bearer error="invalid_token"',
                    },
                ) from e
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            # Issuer module unavailable (minimal-app test, etc.) — fall
            # through to the static path.
            logger.debug(
                "alertmanager webhook: oauth_issuer unavailable; "
                "falling back to static-Bearer comparison"
            )

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
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Dispatch tracking (migration 0137). Set by brain/alert_dispatcher.py
    -- after it polls + sends the row. NULL means "still queued for the
    -- brain to pick up". dispatch_result is 'sent' on success or
    -- 'error: <message>' on failure.
    dispatched_at TIMESTAMPTZ,
    dispatch_result TEXT
);

CREATE INDEX IF NOT EXISTS idx_alert_events_received_at
  ON alert_events (received_at DESC);

CREATE INDEX IF NOT EXISTS idx_alert_events_alertname
  ON alert_events (alertname, received_at DESC);

-- Partial index — only undispatched rows live in the index, so the
-- brain's `WHERE dispatched_at IS NULL` poll stays cheap regardless
-- of total table size.
CREATE INDEX IF NOT EXISTS idx_alert_events_undispatched
  ON alert_events (id)
  WHERE dispatched_at IS NULL;
"""


async def _ensure_table(pool: Any) -> None:
    """Idempotent schema creation — cheap to call on every request."""
    async with pool.acquire() as conn:
        await conn.execute(_ENSURE_TABLE_SQL)


def _parse_iso(val: Any) -> _dt.datetime | None:
    """Coerce an Alertmanager ISO-8601 string to a ``datetime``.

    Alertmanager v2 payloads send ``startsAt`` / ``endsAt`` as JSON
    strings; asyncpg's typed ``$N::timestamptz`` cast refuses string
    input (raises ``invalid input for query argument``). Without
    coercion every webhook insert fails with that message, the
    route's try/except swallows it, and the operator never hears
    about the alert — exactly the silent-failure mode
    ``feedback_no_silent_defaults`` calls out.

    Mirrors the parser in
    ``services/integrations/handlers/webhook_alertmanager.py`` —
    consolidated copy because that module's import depth would create
    a circular path for this route file.
    """
    if not val:
        return None
    if hasattr(val, "isoformat"):
        return val
    if isinstance(val, str):
        if val.startswith("0001-01-01"):  # Alertmanager "never ended" sentinel
            return None
        s = val.rstrip("Z")
        if not any(c in s[10:] for c in ("+", "-")):
            s = s + "+00:00"
        try:
            return _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


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
            _parse_iso(alert.get("startsAt")),
            _parse_iso(alert.get("endsAt")),
            alert.get("fingerprint"),
        )


# ---------------------------------------------------------------------------
# Pageability + formatting helpers (used by the route AND the brain
# dispatcher; kept here so the worker-side operator UI can re-use the
# same shaping when displaying historical alert_events rows).
# ---------------------------------------------------------------------------


def _should_page_operator(alert: dict[str, Any]) -> bool:
    """True when a human should see this in Telegram/Discord immediately.

    Used by the webhook to count + report ``pageable`` alerts. The
    brain's dispatcher does its own severity check inside
    ``poll_and_dispatch`` and routes via ``critical=`` on notify_operator.
    """
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


# NOTE: ``_dispatch_to_operator`` was deleted when dispatch responsibility
# moved to the brain daemon (see module docstring). The
# ``_format_alert_message`` and ``_should_page_operator`` helpers above
# are retained for the operator UI + tests; the brain daemon ships its
# own copy of ``_format_alert_message`` (see ``brain/alert_dispatcher.py``)
# so the brain image stays decoupled from the worker's source tree.


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
    pageable = 0
    remediated = 0
    insert_errors: list[str] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue

        try:
            await _insert_alert(pool, alert)
            persisted += 1
        except Exception as e:
            # Per ``feedback_no_silent_defaults``: don't swallow.
            # Track the failure; if ANY alert failed to persist we
            # return 5xx so Alertmanager's webhook retry kicks in.
            # Pre-2026-05-17 this just logged a warning and returned
            # 200 — Alertmanager treated the call as successful, hit
            # its 4h ``repeat_interval`` for re-tries, and the
            # operator never heard about the missing alert.
            alertname = (alert.get("labels") or {}).get("alertname", "?")
            insert_errors.append(f"{alertname}: {e}")
            logger.warning(
                "alertmanager webhook: insert failed for %s: %s",
                alertname, e,
            )

        # Dispatch is owned by the brain daemon (brain/alert_dispatcher.py)
        # which polls undispatched alert_events rows on a 30s cadence.
        # We still count "would-page" alerts so the response carries an
        # observable signal for callers that want to verify routing —
        # but no outbound notification happens here.
        if _should_page_operator(alert):
            pageable += 1

        try:
            rem = await _maybe_remediate(pool, alert)
            if rem:
                remediated += 1
        except Exception as e:
            logger.warning("alertmanager webhook: remediation lookup failed: %s", e)

    logger.info(
        "alertmanager webhook: received=%d persisted=%d pageable=%d remediated=%d errors=%d",
        len(alerts), persisted, pageable, remediated, len(insert_errors),
    )
    if insert_errors:
        # Alertmanager retries on 5xx but treats 4xx as "client bug,
        # give up". Insert-failures are server-side so 503 is the
        # right shape: persistent failure should still page the
        # operator (Alertmanager surfaces failed notifications in its
        # own UI) but a transient blip will resolve on retry.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"alert_events insert failed for {len(insert_errors)} of "
                f"{len(alerts)} alerts: {insert_errors[0]}"
            ),
        )
    return {
        "ok": True,
        "count": len(alerts),
        "persisted": persisted,
        # Backward-compatible field name; semantics changed from
        # "actually paged" to "would have been paged" since the brain
        # owns dispatch now. ``pageable`` is the new, accurate name.
        "paged": pageable,
        "pageable": pageable,
        "remediated": remediated,
    }
