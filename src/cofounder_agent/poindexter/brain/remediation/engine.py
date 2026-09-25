"""Firefighter engine — decide + act + record + verify. Brain-side only; writes
audit_log directly (emit_finding is worker-side and unavailable here).
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from poindexter.brain.remediation import rules as R
from poindexter.brain.remediation.registry import (
    ActionResult,
    RemediationContext,
    describe_catalog,
    execute,
    refusal,
)

# Value of an alert's ``remediation`` label that keeps it off the LLM long-tail
# path: only an operator-written remediation_rules row may act on it.
RULES_ONLY = "rules_only"

# How the verify scan tells a fixed alert from a live one, recorded on every
# remediation_action as ``verify_signal`` (see verify_signal_for).
VERIFY_BY_REFIRE = "refire"
VERIFY_BY_RESOLVED_NOTIFICATION = "resolved_notification"


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


def verify_signal_for(alert_event: dict[str, Any] | None) -> str:
    """How the verify can tell whether an action on this alert worked.

    Two kinds of producer write ``alert_events``, and silence means opposite
    things from each:

    * A probe that reports a level (the container health watch, most brain
      probes, findings) re-fires every cycle while the problem lasts. After a
      fix, silence is the evidence: no firing row since the action.
    * A notifier that reports changes (Prometheus Alertmanager, and Grafana
      alerting; both post to ``/api/webhooks/alertmanager``) sends a firing
      notification once, repeats it only every ``repeat_interval`` (4 h and
      1 h here), and sends a resolved notification when the alert clears.
      Silence proves nothing. The resolved notification is the evidence.

    The row says which one wrote it. A notifier reports when the alert's
    episode began: every notification of one episode carries the same
    ``starts_at``, and it is never the moment the row was written. A probe
    leaves ``starts_at`` NULL or stamps ``NOW()`` in the same INSERT, which
    makes it equal to ``received_at`` (one transaction timestamp). On prod this
    split the table exactly along the webhook (2026-09-25: 1,810 webhook rows,
    9,541 probe and findings rows, no exception either way). A probe that one
    day writes a real episode start is read as a notifier, and its verify then
    waits for a resolved row and pages without one. That failure is loud.
    """
    event = alert_event or {}
    starts_at = _coerce_dt(event.get("starts_at"))
    if starts_at is None:
        return VERIFY_BY_REFIRE
    if starts_at == _coerce_dt(event.get("received_at")):
        return VERIFY_BY_REFIRE
    return VERIFY_BY_RESOLVED_NOTIFICATION


def _verify_target(alert_event: dict[str, Any] | None) -> dict[str, Any]:
    """The fields a remediation_action records so the verify can read evidence
    about the alert: the row acted on, the producer's own fingerprint and the
    severity of the dedup key, and which signal counts.

    Empty when the row has no producer fingerprint (the legacy hash-of-message
    path): there is then nothing to look the alert up by in ``alert_events``,
    and the verify falls back to ``alert_dedup_state``.
    """
    event = alert_event or {}
    stored_fingerprint = str(event.get("fingerprint") or "").strip()
    if not stored_fingerprint or event.get("id") is None:
        return {}
    return {
        "verify_signal": verify_signal_for(event),
        "alert_event_id": event["id"],
        "alert_fingerprint": stored_fingerprint,
        "alert_severity": str(event.get("severity") or ""),
    }


def _default_verify_after(config: dict[str, Any], target: dict[str, Any]) -> int:
    """The grace before the verify, for a rule (or LLM pick) that sets none.

    A resolved notification cannot arrive before the notifier's next
    ``group_interval`` tick (5 min), so the general default (120 s) would judge
    every fix of an Alertmanager alert still firing.
    """
    if target.get("verify_signal") == VERIFY_BY_RESOLVED_NOTIFICATION:
        return int(config["alertmanager_verify_after_seconds"])
    return int(config["verify_after_seconds"])


async def _apply_action(
    pool: Any, *, alert: dict[str, Any], alertname: str, fingerprint: str,
    config: dict[str, Any], logger: Any, action_name: str, params: dict[str, Any],
    source: str, verify_after: int, max_attempts: int, window_minutes: int,
    rule_id: Any = None, extra_details: dict[str, Any] | None = None,
    verify_target: dict[str, Any] | None = None, dry_run: bool = False,
) -> RemediationDecision:
    """Gate (allowlist -> breaker -> global rate) then execute + audit.

    The single execution path shared by BOTH the deterministic rule source and
    the LLM long-tail source, so a pick from either runs through identical
    safety machinery and produces identically-shaped ``remediation_action`` /
    ``remediation_verify`` audit rows. ``source`` ("rule"|"llm") is recorded in
    the audit details (the Grafana rule-vs-LLM split reads it); ``extra_details``
    carries the LLM-only fields (confidence / reason / model), and
    ``verify_target`` what the verify reads (see ``_verify_target``).

    ``dry_run`` stops after the gates: the pick is recorded as a
    ``remediation_dry_run`` audit row instead of executed, along with the
    executor's own refusal when it would have refused (the restart denylist).
    That event type is invisible to the breaker, the rate cap and the verify
    scan, which count only real ``remediation_action`` rows.

    acted=True  -> action ran OK; the dispatcher HOLDS the page for verify.
    acted=False -> gate rejection, dry run, or non-ok execution; page now.
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

    source_label = f"firefighter:{alertname or 'alert'}"
    ctx = RemediationContext(pool=pool, alert=alert, logger=logger)
    if dry_run:
        refused = await refusal(action_name, params, ctx)
        await _write_audit(
            pool, event_type="remediation_dry_run", source=source_label, severity="info",
            details={
                "fingerprint": fingerprint, "alertname": alertname,
                "action_name": action_name, "params": params, "source": source,
                "verify_after_seconds": verify_after, "refused": refused,
                **(verify_target or {}), **(extra_details or {}),
            },
        )
        logger.info(
            "[firefighter] dry run alert=%s action=%s params=%s source=%s%s — not executed",
            alertname, action_name, params, source,
            f" (the executor would refuse: {refused})" if refused else "",
        )
        if refused:
            reason = f"dry run; the executor would refuse it: {refused}"
        else:
            target = f" with {json.dumps(params, sort_keys=True, default=str)}" if params else ""
            reason = f"dry run; would have run it{target}"
        return RemediationDecision(
            acted=False, action_name=action_name, params=params, source=source,
            reason=reason[:200],
        )

    run_id = str(uuid.uuid4())
    result = await execute(action_name, params, ctx)

    details: dict[str, Any] = {
        "remediation_run_id": run_id, "fingerprint": fingerprint, "alertname": alertname,
        "action_name": action_name, "params": params, "source": source,
        "verify_after_seconds": verify_after,
        "execution": {"status": result.status, "detail": result.detail, "latency_ms": result.latency_ms},
    }
    if rule_id is not None:
        details["rule_id"] = rule_id
    if verify_target:
        details.update(verify_target)
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


def is_persistent(config: dict[str, Any], *, repeat_count: int, age_minutes: float) -> bool:
    """The LLM long-tail's persistence gate: ``repeat_count >= min_repeats`` or
    ``age_minutes >= min_age_minutes`` (a ``min_age_minutes`` of 0 disables the
    age half). The dispatcher asks the same question of the previous repeat to
    find the one row where an alert BECOMES persistent, so both sides share it.
    """
    min_repeats = int(config.get("min_repeats", 2) or 0)
    min_age = int(config.get("min_age_minutes", 0) or 0)
    return repeat_count >= min_repeats or (min_age > 0 and age_minutes >= min_age)


def llm_verify_after(
    config: dict[str, Any], verify_target: dict[str, Any],
    refire_interval_seconds: float | None,
) -> int:
    """How long an LLM-picked action waits before its verify.

    For a probe the verify calls an action resolved when the alert has not
    re-fired since. That only means something once a re-fire was due. On prod
    the persistent repeat arrives a median 44 minutes after a run's first row
    (90 days to 2026-09-25), so the first 120 s after an action are silent
    whether or not it worked: replayed with a model that always picks a
    restart, 1,271 of 1,347 LLM actions judged at the flat
    ``verify_after_seconds`` read "resolved". So the window is
    ``llm_verify_intervals`` of the alert's own re-fire interval (0 = the flat
    window), and never shorter than the default grace. A notifier's resolved
    notification is evidence whenever it comes, so its grace stays the default
    (``_default_verify_after``). A rule sets its own window; its author knows
    the producer.
    """
    floor = _default_verify_after(config, verify_target)
    if verify_target.get("verify_signal") == VERIFY_BY_RESOLVED_NOTIFICATION:
        return floor
    if not refire_interval_seconds or refire_interval_seconds <= 0:
        return floor
    intervals = float(config.get("llm_verify_intervals", 2) or 0)
    return max(floor, math.ceil(intervals * refire_interval_seconds))


async def _select_and_apply(
    pool: Any, *, alert: dict[str, Any], alertname: str, fingerprint: str,
    config: dict[str, Any], logger: Any, select_fn: Any,
    repeat_count: int, age_minutes: float, verify_target: dict[str, Any],
    refire_interval_seconds: float | None = None,
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
    valid pick flows through :func:`_apply_action` with ``source="llm"``, as a
    dry run while ``llm_dry_run`` is on (the default until an operator
    graduates the long-tail).
    """
    if not config.get("llm_longtail_enabled", True):
        return RemediationDecision(acted=False, reason="no rule; llm long-tail disabled")

    if not is_persistent(config, repeat_count=repeat_count, age_minutes=age_minutes):
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
    dry_run = bool(config.get("llm_dry_run", True))
    logger.info(
        "[firefighter] llm selected alert=%s action=%s confidence=%.2f — %s",
        alertname, action_name, confidence, "dry run" if dry_run else "applying",
    )
    return await _apply_action(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, action_name=action_name, params=params,
        source="llm",
        verify_after=llm_verify_after(config, verify_target, refire_interval_seconds),
        max_attempts=config["max_attempts_per_window"],
        window_minutes=config["window_minutes"],
        extra_details={
            "confidence": confidence,
            "reason": str(selection.get("reason") or "")[:500],
            "model": str(selection.get("model") or ""),
            "repeat_count": repeat_count,
            "age_minutes": round(float(age_minutes or 0), 1),
        },
        verify_target=verify_target, dry_run=dry_run,
    )


def _not_actionable(alert: dict[str, Any], config: dict[str, Any]) -> RemediationDecision | None:
    """The refusal both entry points share, or None when the alert may be acted on.

    Only a FIRING alert describes a problem to fix. A resolved row for the
    same alertname (probes write recovery rows; Alertmanager sends resolved
    notifications) would otherwise match the same rule and re-run its action
    against something that just recovered. The dispatcher defaults a missing
    status to "firing", so absent means firing here too.
    """
    if not config.get("enabled"):
        return RemediationDecision(acted=False, reason="disabled")
    status = str(alert.get("status") or "firing").strip().lower()
    if status != "firing":
        return RemediationDecision(acted=False, reason=f"status {status}; nothing to remediate")
    return None


async def _long_tail(
    pool: Any, *, alert: dict[str, Any], alertname: str, fingerprint: str,
    config: dict[str, Any], logger: Any, select_fn: Any,
    repeat_count: int, age_minutes: float, verify_target: dict[str, Any],
    refire_interval_seconds: float | None = None,
) -> RemediationDecision:
    """The no-rule branch: the gated LLM selector, when one is wired."""
    if select_fn is None:
        return RemediationDecision(acted=False, reason="no rule")
    # A producer whose alert covers targets that must never be bounced blind
    # (the container health watch fires for GPU renderers mid-job and for a
    # busy worker) marks it rules-only: an operator-written rule may act on it,
    # the LLM selector may not.
    labels = alert.get("labels") or {}
    if str(labels.get("remediation") or "").strip().lower() == RULES_ONLY:
        return RemediationDecision(acted=False, reason="no rule; alert allows rule-driven remediation only")
    return await _select_and_apply(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, select_fn=select_fn,
        repeat_count=repeat_count, age_minutes=age_minutes,
        verify_target=verify_target, refire_interval_seconds=refire_interval_seconds,
    )


async def evaluate_for_dispatch(
    pool: Any, *, alert: dict[str, Any], fingerprint: str,
    config: dict[str, Any], logger: Any,
    select_fn: Any = None, repeat_count: int = 0, age_minutes: float = 0,
    alert_event: dict[str, Any] | None = None,
) -> RemediationDecision:
    """Decide whether to remediate an about-to-page alert (rules first, LLM tail).

    The dispatcher calls this on the first row of an episode. A matched
    ``remediation_rules`` row runs deterministically. With no rule and a
    ``select_fn`` wired (Plan B), the gated LLM long-tail path may pick an
    action; without a ``select_fn`` the no-rule alert pages as before.

    ``alert_event`` is the ``alert_events`` row being dispatched (``id``, the
    producer's ``fingerprint``, the dedup key's ``severity``, ``starts_at``,
    ``received_at``). The verify reads its evidence by it; without one it falls
    back to ``alert_dedup_state``.

    acted=True  -> an action ran OK; the dispatcher must HOLD the page and let
                   the verify scan resolve/escalate it later.
    acted=False -> page as usual (no rule, disabled, gate tripped, LLM
                   abstained/low-confidence, or the action failed to run).
    """
    refused = _not_actionable(alert, config)
    if refused is not None:
        return refused

    labels = alert.get("labels") or {}
    alertname = (labels.get("alertname") or "").strip()
    verify_target = _verify_target(alert_event)
    rule = await R.match_rule(pool, alertname=alertname, fingerprint=fingerprint)
    if rule is not None:
        return await _apply_action(
            pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
            config=config, logger=logger, action_name=rule["action_name"],
            params=rule["params"], source="rule",
            verify_after=rule["verify_after_seconds"] or _default_verify_after(config, verify_target),
            max_attempts=rule["max_attempts_per_window"] or config["max_attempts_per_window"],
            window_minutes=rule["window_minutes"] or config["window_minutes"],
            rule_id=rule["id"], verify_target=verify_target,
        )

    # No deterministic rule. Fall back to the gated LLM long-tail path when a
    # selector is wired; otherwise page as before (unchanged back-compat).
    return await _long_tail(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, select_fn=select_fn,
        repeat_count=repeat_count, age_minutes=age_minutes,
        verify_target=verify_target,
    )


async def evaluate_persistent_for_dispatch(
    pool: Any, *, alert: dict[str, Any], fingerprint: str,
    config: dict[str, Any], logger: Any, select_fn: Any,
    repeat_count: int, age_minutes: float,
    refire_interval_seconds: float | None = None,
    alert_event: dict[str, Any] | None = None,
) -> RemediationDecision:
    """Offer an alert to the LLM long-tail on the repeat where it became persistent.

    The first row of an episode goes through :func:`evaluate_for_dispatch`, and
    the long-tail's persistence gate always refuses it: it is a first sighting.
    Persistence arrives on a LATER repeat, which the dispatcher suppresses (the
    first row already paged), so without this second look the long-tail never
    ran at all (glad-labs-stack#4022). The dispatcher calls this once, on the
    row where the alert crosses the gate.

    Rules are not consulted twice. A rule had the episode's first row, and
    either acted (a verify is pending) or declined (the operator was paged with
    the reason), so a matching rule here means "not the long-tail's alert" and
    nothing runs. Everything else — the rules-only label, the exclusion regex,
    the confidence floor, the allowlist, the circuit breaker, the global rate
    cap and the restart denylist — applies exactly as on a first row, and the
    verify reads ``alert_event`` (this row) as it does there.
    """
    refused = _not_actionable(alert, config)
    if refused is not None:
        return refused

    labels = alert.get("labels") or {}
    alertname = (labels.get("alertname") or "").strip()
    if await R.match_rule(pool, alertname=alertname, fingerprint=fingerprint) is not None:
        return RemediationDecision(acted=False, reason="rule-matched; its rule had the episode's first row")
    return await _long_tail(
        pool, alert=alert, alertname=alertname, fingerprint=fingerprint,
        config=config, logger=logger, select_fn=select_fn,
        repeat_count=repeat_count, age_minutes=age_minutes,
        verify_target=_verify_target(alert_event),
        refire_interval_seconds=refire_interval_seconds,
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
    """Legacy oracle: True iff the dedup key was seen again after we acted.

    Only for an action that recorded no ``alert_fingerprint`` (written before
    the verify read ``alert_events``, or an alert with no producer
    fingerprint). It is blind twice over, which is why it is only a fallback:
    Alertmanager re-sends a live alert only every ``repeat_interval``, so no
    advance proves nothing; and its resolved notification shares the firing
    row's dedup key, so an advance can be the alert clearing.

    The dispatcher bumps alert_dedup_state.last_seen_at on every (suppressed)
    repeat, keyed by the SAME fingerprint the engine stored. No dedup row ->
    treat as resolved (fail toward silence; the next real fire re-pages through
    the normal path).

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


# A probe re-fired: a firing row of the dedup key (the producer's fingerprint
# plus the key's severity) received after the action. The bound on the id of
# the row acted on keeps the scan on the primary key.
_REFIRED_SINCE_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM alert_events
    WHERE id > $1
      AND fingerprint = $2
      AND lower(status) = 'firing'
      AND COALESCE(severity, '') = $3
      AND received_at > $4
)
"""

# What a notifier last said about the alert since the row acted on: 'firing'
# (at the dedup key's severity), 'resolved', or NULL when it has said nothing.
_LATEST_NOTIFICATION_SQL = """
SELECT lower(status)
FROM alert_events
WHERE id > $1
  AND fingerprint = $2
  AND (lower(status) = 'resolved'
       OR (lower(status) = 'firing' AND COALESCE(severity, '') = $3))
ORDER BY id DESC
LIMIT 1
"""

# Why the verify judged an action the way it did, recorded as ``evidence`` on
# the remediation_verify row; the ones worth saying in a page get a phrase.
_EVIDENCE_PAGE_NOTES = {
    "refired": "it fired again after the action",
    "no_resolved_notification": "no resolved notification since the action",
    "check_failed": "the verify could not read the alert's state",
}


async def _judge_attempt(
    pool: Any, *, details: dict[str, Any], acted_at: datetime,
) -> tuple[bool, str]:
    """``(still_firing, evidence)`` for one pending action.

    Reads ``alert_events`` for the alert the action was taken on, by the
    producer's own fingerprint. Only a FIRING row at the dedup key's severity
    is a re-fire. A resolved row never is: Alertmanager's resolved notification
    shares the firing row's dedup key, which is how the legacy oracle could
    read a recovery as "still firing". What proves a fix depends on the
    producer (``verify_signal``, see ``verify_signal_for``):

    * ``refire``: a firing row received after the action means the fix did not
      hold (``refired``), and a recovery row after it does not undo that. None
      means it held (``no_refire``).
    * ``resolved_notification``: the notifier's latest word since the row acted
      on decides. ``resolved`` means fixed (``resolved_notification``).
      ``firing`` means it came back or never cleared (``refired``). Nothing
      means it has not reported the alert cleared, so it is still firing
      (``no_resolved_notification``). A notification between the acted row and
      the action counts too: each one is a change of state, so one that landed
      while the brain worked through a backlog is still the latest state.

    An action without an ``alert_fingerprint`` falls back to the legacy
    ``alert_dedup_state`` oracle (``dedup_state``). A DB error propagates.
    """
    stored_fingerprint = str(details.get("alert_fingerprint") or "")
    event_id = details.get("alert_event_id")
    if not stored_fingerprint or event_id is None:
        still = await _alert_still_firing(
            pool, fingerprint=details.get("fingerprint") or "", since=acted_at,
        )
        return still, "dedup_state"
    severity = str(details.get("alert_severity") or "")
    if details.get("verify_signal") == VERIFY_BY_RESOLVED_NOTIFICATION:
        latest = await pool.fetchval(
            _LATEST_NOTIFICATION_SQL, int(event_id), stored_fingerprint, severity,
        )
        if latest == "resolved":
            return False, "resolved_notification"
        if latest == "firing":
            return True, "refired"
        return True, "no_resolved_notification"
    refired = await pool.fetchval(
        _REFIRED_SINCE_SQL, int(event_id), stored_fingerprint, severity, acted_at,
    )
    return (True, "refired") if refired else (False, "no_refire")


@dataclass
class RemediationAttempt:
    """The latest ``remediation_action`` for a fingerprint, with its outcome.

    ``verify_result`` is None while the verify scan has not judged the action
    yet; otherwise it is the terminal ``remediation_verify`` result
    (``resolved`` / ``still_firing`` / ``action_failed``).
    """

    run_id: str
    action_name: str
    acted_at: datetime
    verify_result: str | None
    verified_at: datetime | None


_LATEST_ATTEMPT_SQL = """
SELECT a.timestamp AS acted_at,
       a.details->>'remediation_run_id' AS run_id,
       a.details->>'action_name' AS action_name,
       v.timestamp AS verified_at,
       v.details->>'result' AS verify_result
FROM audit_log a
LEFT JOIN LATERAL (
    SELECT vv.timestamp, vv.details
    FROM audit_log vv
    WHERE vv.event_type = 'remediation_verify'
      AND vv.details->>'remediation_run_id' = a.details->>'remediation_run_id'
    ORDER BY vv.id DESC
    LIMIT 1
) v ON TRUE
WHERE a.event_type = 'remediation_action'
  AND a.details->>'fingerprint' = $1
ORDER BY a.id DESC
LIMIT 1
"""


async def latest_attempt(pool: Any, *, fingerprint: str) -> RemediationAttempt | None:
    """The fingerprint's most recent remediation attempt, or None if it has none.

    The dispatcher reads this to find where one episode of an alert ends and the
    next begins inside a dedup window (a verified fix ends an episode). A DB
    error propagates: the caller decides what an unknown history means.
    """
    row = await pool.fetchrow(_LATEST_ATTEMPT_SQL, fingerprint)
    if not row:
        return None
    rd = dict(row)
    acted_at = _coerce_dt(rd.get("acted_at"))
    if acted_at is None:
        return None
    verify_result = rd.get("verify_result")
    return RemediationAttempt(
        run_id=str(rd.get("run_id") or ""),
        action_name=str(rd.get("action_name") or ""),
        acted_at=acted_at,
        verify_result=str(verify_result) if verify_result else None,
        verified_at=_coerce_dt(rd.get("verified_at")),
    )


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
    run_id. For each past its verify_after_seconds, ``_judge_attempt`` reads the
    evidence: resolved -> silent; still firing -> page + write the verify row
    (so the breaker counts it next time). The verify row records the
    ``evidence`` either way. Best-effort: never raises into the poll loop.
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
            still, evidence = await _judge_attempt(pool, details=details, acted_at=acted_at)
        except Exception as e:  # noqa: BLE001
            logger.warning("[firefighter] still-firing check failed for run=%s: %s", run_id, e)
            still, evidence = True, "check_failed"
        if still:
            summary["still_firing"] += 1
            await _write_audit(
                pool, event_type="remediation_verify", source=alertname, severity="warning",
                details={
                    "remediation_run_id": run_id, "result": "still_firing",
                    "evidence": evidence, "checked_at": now.isoformat(),
                },
            )
            note = _EVIDENCE_PAGE_NOTES.get(evidence)
            msg = (
                f"[FIREFIGHTER] auto-remediation did not resolve {alertname}: "
                f"attempted {action}, still firing after {verify_after}s"
                f"{f' ({note})' if note else ''}"
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
                details={
                    "remediation_run_id": run_id, "result": "resolved",
                    "evidence": evidence, "checked_at": now.isoformat(),
                },
            )
            logger.info(
                "[firefighter] resolved alert=%s action=%s run=%s evidence=%s (silent)",
                alertname, action, str(run_id)[:8], evidence,
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
