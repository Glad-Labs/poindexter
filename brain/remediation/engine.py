"""Firefighter engine — decide + act + record + verify. Brain-side only; writes
audit_log directly (emit_finding is worker-side and unavailable here).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from brain.remediation import rules as R
from brain.remediation.registry import ActionResult, RemediationContext, execute


@dataclass
class RemediationDecision:
    acted: bool
    action_name: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # "rule" (Plan A); "llm" (Plan B)
    run_id: str | None = None
    result: ActionResult | None = None
    reason: str = ""


async def _write_audit(
    pool: Any, *, event_type: str, source: str, severity: str,
    details: dict[str, Any], task_id: str | None = None,
) -> None:
    """Insert one audit_log row. Matches services.audit_log.AuditLogger.log shape."""
    await pool.execute(
        "INSERT INTO audit_log (event_type, source, task_id, details, severity) "
        "VALUES ($1, $2, $3, $4::jsonb, $5)",
        event_type, source, task_id, json.dumps(details, default=str), severity,
    )


async def evaluate_for_dispatch(
    pool: Any, *, alert: dict[str, Any], fingerprint: str,
    config: dict[str, Any], logger: Any,
) -> RemediationDecision:
    """Deterministic rule path, evaluated when an alert is about to be paged.

    acted=True  -> an action ran OK; the dispatcher must HOLD the page and let
                   the verify scan resolve/escalate it later.
    acted=False -> page as usual (no rule, disabled, breaker/rate tripped, or
                   the action failed to run so waiting to verify is pointless).
    """
    if not config.get("enabled"):
        return RemediationDecision(acted=False, reason="disabled")

    alertname = (alert.get("labels", {}).get("alertname") or "").strip()
    rule = await R.match_rule(pool, alertname=alertname, fingerprint=fingerprint)
    if rule is None:
        return RemediationDecision(acted=False, reason="no rule")

    action_name = rule["action_name"]
    allowlist = config.get("action_allowlist") or []
    if allowlist and action_name not in allowlist:
        return RemediationDecision(acted=False, reason=f"action {action_name} not in allowlist")

    max_attempts = rule["max_attempts_per_window"] or config["max_attempts_per_window"]
    window_minutes = rule["window_minutes"] or config["window_minutes"]
    if await R.circuit_breaker_tripped(
        pool, fingerprint=fingerprint, action_name=action_name,
        max_attempts=max_attempts, window_minutes=window_minutes,
    ):
        return RemediationDecision(acted=False, action_name=action_name, reason="circuit breaker tripped")

    if await R.global_rate_exceeded(pool, max_actions_per_hour=config["max_actions_per_hour"]):
        return RemediationDecision(acted=False, action_name=action_name, reason="global rate cap")

    run_id = str(uuid.uuid4())
    verify_after = rule["verify_after_seconds"] or config["verify_after_seconds"]
    ctx = RemediationContext(pool=pool, alert=alert, logger=logger)
    result = await execute(action_name, rule["params"], ctx)

    source_label = f"firefighter:{alertname or 'alert'}"
    await _write_audit(
        pool, event_type="remediation_action", source=source_label, severity="info",
        details={
            "remediation_run_id": run_id, "fingerprint": fingerprint, "alertname": alertname,
            "action_name": action_name, "params": rule["params"], "source": "rule",
            "rule_id": rule["id"], "verify_after_seconds": verify_after,
            "execution": {"status": result.status, "detail": result.detail, "latency_ms": result.latency_ms},
        },
    )

    if result.status == "ok":
        logger.info(
            "[firefighter] acted alert=%s action=%s run=%s — holding page for verify",
            alertname, action_name, run_id[:8],
        )
        return RemediationDecision(
            acted=True, action_name=action_name, params=rule["params"],
            source="rule", run_id=run_id, result=result, reason="rule matched",
        )

    # Action did not run OK -> nothing to verify; write a terminal verify row so
    # the verify scan skips it, and page now.
    await _write_audit(
        pool, event_type="remediation_verify", source=source_label, severity="warning",
        details={
            "remediation_run_id": run_id, "result": "action_failed",
            "checked_at": datetime.now(UTC).isoformat(),
            "detail": result.detail,
        },
    )
    return RemediationDecision(
        acted=False, action_name=action_name, source="rule", run_id=run_id,
        result=result, reason=f"action {result.status}: {result.detail}"[:200],
    )


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _coerce_details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


async def _alert_still_firing(pool: Any, *, fingerprint: str, since: datetime) -> bool:
    """True iff the alert re-fired after we acted.

    The dispatcher bumps alert_dedup_state.last_seen_at on every (suppressed)
    repeat, keyed by the SAME fingerprint the engine stored. So last_seen_at
    advancing past `since` means the problem is still live; no advance means it
    stopped firing (resolved). No dedup row -> treat as resolved (fail toward
    silence; the next real fire re-pages through the normal path).

    A DB read failure is deliberately NOT swallowed here: it propagates to
    run_verify_scan's handler, which logs a warning and treats the alert as
    still firing (page rather than silently drop) — so the failure stays
    visible instead of being hidden behind a silent ``return True``.
    """
    row = await pool.fetchrow(
        "SELECT last_seen_at FROM alert_dedup_state WHERE fingerprint = $1",
        fingerprint,
    )
    if not row:
        return False
    last_seen = _coerce_dt(
        row.get("last_seen_at") if isinstance(row, dict) else row["last_seen_at"]
    )
    if last_seen is None:
        return False
    return last_seen > since


_VERIFY_PENDING_SQL = """
SELECT a.id, a.timestamp, a.details
FROM audit_log a
WHERE a.event_type = 'remediation_action'
  AND NOT EXISTS (
      SELECT 1 FROM audit_log v
      WHERE v.event_type = 'remediation_verify'
        AND v.details->>'remediation_run_id' = a.details->>'remediation_run_id'
  )
ORDER BY a.id ASC
LIMIT 50
"""


async def run_verify_scan(
    pool: Any, *, config: dict[str, Any], logger: Any, notify_fn: Any = None,
) -> dict[str, int]:
    """Resolve pending remediation actions past their grace period.

    Pending = a remediation_action row with no remediation_verify sharing its
    run_id. For each past its verify_after_seconds: resolved -> silent; still
    firing -> page + write the verify row (so the breaker counts it next time).
    Best-effort: never raises into the poll loop.
    """
    summary = {"verified": 0, "resolved": 0, "still_firing": 0}
    try:
        rows = await pool.fetch(_VERIFY_PENDING_SQL)
    except Exception as e:  # noqa: BLE001
        logger.warning("[firefighter] verify scan poll failed: %s", e)
        return summary

    now = datetime.now(UTC)
    for r in rows:
        rd = dict(r)
        details = _coerce_details(rd.get("details"))
        run_id = details.get("remediation_run_id")
        acted_at = _coerce_dt(rd.get("timestamp")) or now
        verify_after = int(details.get("verify_after_seconds") or config["verify_after_seconds"])
        if (now - acted_at).total_seconds() < verify_after:
            continue  # not yet due
        fingerprint = details.get("fingerprint") or ""
        alertname = details.get("alertname") or "firefighter"
        action = details.get("action_name") or "?"
        summary["verified"] += 1
        try:
            still = await _alert_still_firing(pool, fingerprint=fingerprint, since=acted_at)
        except Exception as e:  # noqa: BLE001
            logger.warning("[firefighter] still-firing check failed for run=%s: %s", run_id, e)
            still = True
        if still:
            summary["still_firing"] += 1
            await _write_audit(
                pool, event_type="remediation_verify", source=alertname, severity="warning",
                details={"remediation_run_id": run_id, "result": "still_firing", "checked_at": now.isoformat()},
            )
            msg = (
                f"[FIREFIGHTER] auto-remediation did not resolve {alertname}: "
                f"attempted {action}, still firing after {verify_after}s"
            )
            if notify_fn is not None:
                try:
                    await notify_fn(msg, critical=False)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[firefighter] verify page failed for run=%s: %s", run_id, e)
        else:
            summary["resolved"] += 1
            await _write_audit(
                pool, event_type="remediation_verify", source=alertname, severity="info",
                details={"remediation_run_id": run_id, "result": "resolved", "checked_at": now.isoformat()},
            )
            logger.info(
                "[firefighter] resolved alert=%s action=%s run=%s (silent)",
                alertname, action, str(run_id)[:8],
            )
    return summary
