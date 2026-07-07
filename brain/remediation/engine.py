"""Firefighter engine — decide + act + record + verify. Brain-side only; writes
audit_log directly (emit_finding is worker-side and unavailable here).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from brain.remediation import rules as R
from brain.remediation.registry import (
    ActionResult,
    RemediationContext,
    describe_catalog,
    execute,
)


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


async def _apply_action(
    pool: Any, *, alert: dict[str, Any], alertname: str, fingerprint: str,
    config: dict[str, Any], logger: Any, action_name: str, params: dict[str, Any],
    source: str, verify_after: int, max_attempts: int, window_minutes: int,
    rule_id: Any = None, extra_details: dict[str, Any] | None = None,
) -> RemediationDecision:
    """Gate (allowlist -> breaker -> global rate) then execute + audit.

    The single execution path shared by BOTH the deterministic rule source and
    the LLM long-tail source, so a pick from either runs through identical
    safety machinery and produces identically-shaped ``remediation_action`` /
    ``remediation_verify`` audit rows. ``source`` ("rule"|"llm") is recorded in
    the audit details (the Grafana rule-vs-LLM split reads it); ``extra_details``
    carries the LLM-only fields (confidence / reason / model).

    acted=True  -> action ran OK; the dispatcher HOLDS the page for verify.
    acted=False -> gate rejection or non-ok execution; page now.
    """
    allowlist = config.get("action_allowlist") or []
    if allowlist and action_name not in allowlist:
        return RemediationDecision(
            acted=False, action_name=action_name, source=source,
            reason=f"action {action_name} not in allowlist",
        )

    if await R.circuit_breaker_tripped(
        pool, fingerprint=fingerprint, action_name=action_name,
        max_attempts=max_attempts, window_minutes=window_minutes,
    ):
        return RemediationDecision(
            acted=False, action_name=action_name, source=source,
            reason="circuit breaker tripped",
        )

    if await R.global_rate_exceeded(pool, max_actions_per_hour=config["max_actions_per_hour"]):
        return RemediationDecision(
            acted=False, action_name=action_name, source=source, reason="global rate cap",
        )

    run_id = str(uuid.uuid4())
    ctx = RemediationContext(pool=pool, alert=alert, logger=logger)
    result = await execute(action_name, params, ctx)

    source_label = f"firefighter:{alertname or 'alert'}"
    details: dict[str, Any] = {
        "remediation_run_id": run_id, "fingerprint": fingerprint, "alertname": alertname,
        "action_name": action_name, "params": params, "source": source,
        "verify_after_seconds": verify_after,
        "execution": {"status": result.status, "detail": result.detail, "latency_ms": result.latency_ms},
    }
    if rule_id is not None:
        details["rule_id"] = rule_id
    if extra_details:
        details.update(extra_details)
    await _write_audit(
        pool, event_type="remediation_action", source=source_label, severity="info",
        details=details,
    )

    if result.status == "ok":
        logger.info(
            "[firefighter] acted alert=%s action=%s source=%s run=%s — holding page for verify",
            alertname, action_name, source, run_id[:8],
        )
        return RemediationDecision(
            acted=True, action_name=action_name, params=params,
            source=source, run_id=run_id, result=result, reason=f"{source} matched",
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
        acted=False, action_name=action_name, source=source, run_id=run_id,
        result=result, reason=f"action {result.status}: {result.detail}"[:200],
    )


async def _select_and_apply(
    pool: Any, *, alert: dict[str, Any], alertname: str, fingerprint: str,
    config: dict[str, Any], logger: Any, select_fn: Any,
    repeat_count: int, age_minutes: int,
) -> RemediationDecision:
    """LLM long-tail path (Plan B): a gated, validated selection over the catalog.

    Each gate that fails -> page as usual, no inference call:

    * long-tail master switch off (``llm_longtail_enabled``);
    * not persistent — ``repeat_count < min_repeats`` AND ``age < min_age_minutes``
      (a first-sighting blip pages without burning a model call);
    * alertname matches ``llm_exclude_regex`` — the circular-dependency guard, so
      the model is never asked to fix the substrate it runs on (Ollama/GPU/…).

    Then the selector is asked for ONE catalog action. The pick is re-validated
    here (in-catalog + confidence >= ``min_confidence``) even though the worker
    route already validates — the model's output stays untrusted end-to-end. A
    valid pick flows through :func:`_apply_action` with ``source="llm"``.
    """
    if not config.get("llm_longtail_enabled", True):
        return RemediationDecision(acted=False, reason="no rule; llm long-tail disabled")

    min_repeats = int(config.get("min_repeats", 2) or 0)
    min_age = int(config.get("min_age_minutes", 0) or 0)
    persistent = repeat_count >= min_repeats or (min_age > 0 and age_minutes >= min_age)
    if not persistent:
        return RemediationDecision(acted=False, reason="no rule; alert not persistent yet")

    exclude_regex = config.get("llm_exclude_regex") or ""
    if exclude_regex:
        try:
            excluded = re.search(exclude_regex, alertname or "") is not None
        except re.error as e:
            logger.warning(
                "[firefighter] bad llm_exclude_regex %r: %s — skipping llm path",
                exclude_regex, e,
            )
            return RemediationDecision(acted=False, reason="no rule; bad exclude regex")
        if excluded:
            return RemediationDecision(acted=False, reason="no rule; alert excluded from llm path")

    catalog = describe_catalog(config.get("action_allowlist") or None)
    catalog_names = {c["name"] for c in catalog}
    if not catalog_names:
        return RemediationDecision(acted=False, reason="no rule; empty action catalog")

    try:
        selection = await select_fn(alert=alert, catalog=catalog)
    except Exception as e:  # noqa: BLE001 — selector transport failure = page as usual
        logger.warning("[firefighter] llm select_fn raised: %s — paging", e)
        return RemediationDecision(acted=False, source="llm", reason="no rule; llm selector error")

    if not isinstance(selection, dict):
        return RemediationDecision(acted=False, source="llm", reason="no rule; llm abstained")
    action_name = str(selection.get("action_name") or "").strip()
    if not action_name:
        return RemediationDecision(acted=False, source="llm", reason="no rule; llm abstained")
    if action_name not in catalog_names:
        # Untrusted-output guard (defense in depth — the worker validates too):
        # the model can never make the engine run an action it wasn't offered.
        logger.warning("[firefighter] llm picked off-catalog action %r — refusing", action_name)
        return RemediationDecision(
            acted=False, action_name=action_name, source="llm",
            reason="llm picked off-catalog action",
        )

    try:
        confidence = float(selection.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    min_conf = float(config.get("min_confidence", 0.6) or 0.0)
    if confidence < min_conf:
        return RemediationDecision(
            acted=False, action_name=action_name, source="llm",
            reason=f"llm confidence {confidence:.2f} below {min_conf:.2f}",
        )

    params = selection.get("params")
    if not isinstance(params, dict):
        params = {}
    logger.info(
        "[firefighter] llm selected alert=%s action=%s confidence=%.2f — applying",
        alertname, action_name, confidence,
    )
    return await _apply_action(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, action_name=action_name, params=params,
        source="llm", verify_after=config["verify_after_seconds"],
        max_attempts=config["max_attempts_per_window"],
        window_minutes=config["window_minutes"],
        extra_details={
            "confidence": confidence,
            "reason": str(selection.get("reason") or "")[:500],
            "model": str(selection.get("model") or ""),
            "repeat_count": repeat_count,
        },
    )


async def evaluate_for_dispatch(
    pool: Any, *, alert: dict[str, Any], fingerprint: str,
    config: dict[str, Any], logger: Any,
    select_fn: Any = None, repeat_count: int = 0, age_minutes: int = 0,
) -> RemediationDecision:
    """Decide whether to remediate an about-to-page alert (rules first, LLM tail).

    A matched ``remediation_rules`` row runs deterministically. With no rule and
    a ``select_fn`` wired (Plan B), the gated LLM long-tail path may pick an
    action; without a ``select_fn`` the no-rule alert pages as before.

    acted=True  -> an action ran OK; the dispatcher must HOLD the page and let
                   the verify scan resolve/escalate it later.
    acted=False -> page as usual (no rule, disabled, gate tripped, LLM
                   abstained/low-confidence, or the action failed to run).
    """
    if not config.get("enabled"):
        return RemediationDecision(acted=False, reason="disabled")

    alertname = (alert.get("labels", {}).get("alertname") or "").strip()
    rule = await R.match_rule(pool, alertname=alertname, fingerprint=fingerprint)
    if rule is not None:
        return await _apply_action(
            pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
            config=config, logger=logger, action_name=rule["action_name"],
            params=rule["params"], source="rule",
            verify_after=rule["verify_after_seconds"] or config["verify_after_seconds"],
            max_attempts=rule["max_attempts_per_window"] or config["max_attempts_per_window"],
            window_minutes=rule["window_minutes"] or config["window_minutes"],
            rule_id=rule["id"],
        )

    # No deterministic rule. Fall back to the gated LLM long-tail path when a
    # selector is wired; otherwise page as before (unchanged back-compat).
    if select_fn is None:
        return RemediationDecision(acted=False, reason="no rule")
    return await _select_and_apply(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, select_fn=select_fn,
        repeat_count=repeat_count, age_minutes=age_minutes,
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


async def _write_candidate_rule_finding(
    pool: Any, *, details: dict[str, Any], alertname: str, action: str,
    fingerprint: str, run_id: Any,
) -> None:
    """Emit a ``remediation_candidate_rule`` finding for a resolved LLM self-heal.

    A finding-shaped ``audit_log`` row (matching ``utils.findings.emit_finding``,
    which the brain can't call — it's worker-side) so the fix flows into the
    Findings dashboard + ``findings_list`` triage, where the operator promotes it
    to a durable ``remediation_rules`` row (the learning loop). ``severity=warn``
    routes one Discord nudge per novel ``(alert, action)``; the stable
    ``dedup_key`` keeps repeats quiet.
    """
    await _write_audit(
        pool, event_type="finding", source=f"firefighter:{alertname or 'alert'}",
        severity="warn",
        details={
            "kind": "remediation_candidate_rule",
            "title": f"LLM self-heal worked: {action} resolved {alertname}",
            "body": (
                f"The firefighter's LLM long-tail path chose `{action}` for the "
                f"un-ruled alert `{alertname}` and it resolved. Consider promoting "
                f"this to a remediation_rules row so it runs deterministically "
                f"(no inference call, no persistence wait)."
            ),
            "dedup_key": f"remediation-candidate:{alertname}:{action}",
            "extra": {
                "alertname": alertname,
                "action_name": action,
                "params": details.get("params") or {},
                "confidence": details.get("confidence"),
                "model": details.get("model") or "",
                "fingerprint": fingerprint,
                "remediation_run_id": run_id,
            },
        },
    )


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
            # Learning loop: a RESOLVED llm-source self-heal for an un-ruled
            # alert is a candidate for a durable rule — surface it for the
            # operator to promote. Rule-source resolves are expected behaviour,
            # not candidates, so they emit nothing.
            if details.get("source") == "llm":
                await _write_candidate_rule_finding(
                    pool, details=details, alertname=alertname,
                    action=action, fingerprint=fingerprint, run_id=run_id,
                )
    return summary
