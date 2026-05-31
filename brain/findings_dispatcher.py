"""Findings dispatcher (Glad-Labs/poindexter#461) — Phase 1 MVP.

The second half of the findings architecture. ``utils/findings.emit_finding``
already persists typed findings to ``audit_log`` (``event_type='finding'``,
``details`` = ``{kind, title, body, severity, dedup_key, extra}``). This
module is the brain-daemon-side router: it polls undelivered findings and
routes each per a per-``kind`` policy in ``app_settings``, mirroring the
poll/mark shape of ``brain/alert_dispatcher.py``.

Why a sibling state table (not ``audit_log.processed_at``): ``audit_log`` is
the append-only canonical record — it gets no mutable per-row delivery flag.
``findings_dispatch_state`` records what was delivered (keyed on the
``audit_log`` row id), and the poll is a ``NOT EXISTS`` anti-join. This is
exactly how ``alert_dispatcher`` keeps its own dedup state separate.

First-run safety: the seed migration backfills every pre-existing finding as
already-dispatched, so activating the dispatcher never storms the operator
with the accumulated backlog. Unknown kinds resolve to the ``findings.default``
policy (``log_only``) so a new finding kind can never silently start paging.

Phase 1 scope: the ``discord`` / ``telegram`` / ``log_only`` channels are
implemented via ``notify_operator``. ``auto_fix`` and ``github_issue`` are
deferred to Phase 2 — a policy naming them falls through to its ``fallback``
(then ``log_only``), so an auto-fixable finding still surfaces to the operator
instead of vanishing. Telegram is reserved for policies that explicitly ask
for it AND clear ``min_severity`` (per feedback_telegram_vs_discord).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("brain.findings_dispatcher")

# Channels this MVP can actually deliver to. auto_fix + github_issue are
# Phase 2 — see module docstring.
_IMPLEMENTED_CHANNELS = frozenset({"discord", "telegram", "log_only"})

# Severity ranking for the min_severity gate. emit_finding uses "warn"
# (not "warning"); we accept both, and map alert-style "error" alongside.
_SEVERITY_ORDER = {"info": 0, "warn": 1, "warning": 1, "critical": 2, "error": 2}

# Defaults mirror services/settings_defaults.py + the seed migration. The
# dispatcher reads live values each cycle so an operator can retune without
# restarting the brain.
_DEFAULT_POLICY = {
    "delivery": "log_only",
    "fallback": "log_only",
    "cooldown_minutes": 1440,
    "min_severity": "warn",
}

_POLL_SQL = """
SELECT a.id, a.severity, a.source, a.details
FROM audit_log a
WHERE a.event_type = 'finding'
  AND NOT EXISTS (
        SELECT 1 FROM findings_dispatch_state s WHERE s.finding_id = a.id
  )
ORDER BY a.id ASC
LIMIT $1
"""

_MARK_SQL = """
INSERT INTO findings_dispatch_state
    (finding_id, kind, dedup_key, channel, dispatch_result, dispatched_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (finding_id) DO NOTHING
"""

_RECENT_DELIVERY_SQL = """
SELECT 1
FROM findings_dispatch_state
WHERE kind = $1
  AND dedup_key = $2
  AND dispatch_result = 'sent'
  AND dispatched_at > $3
LIMIT 1
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_policies(pool: Any) -> dict[str, str]:
    """Batch-read every ``findings.*`` app_setting in one round-trip.

    Returns the raw ``key -> value`` map; per-kind resolution happens in
    memory. Missing/empty → the in-code defaults apply.
    """
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key LIKE 'findings.%'"
        )
        return {r["key"]: r["value"] for r in rows}
    except Exception as e:  # noqa: BLE001 — fail-safe to in-code defaults
        logger.debug("[findings_dispatcher] policy load failed (%s) — defaults", e)
        return {}


def _resolve_policy(kind: str, raw: dict[str, str]) -> dict[str, Any]:
    """Merge the per-kind policy over ``findings.default`` over the in-code
    defaults. Each field falls back independently so a partially-specified
    kind policy still gets sane values."""
    out = dict(_DEFAULT_POLICY)

    def _apply(prefix: str) -> None:
        for field in ("delivery", "fallback", "cooldown_minutes", "min_severity"):
            val = raw.get(f"{prefix}.{field}")
            if val is not None and val != "":
                out[field] = val

    _apply("findings.default")
    _apply(f"findings.{kind}")

    try:
        out["cooldown_minutes"] = int(str(out["cooldown_minutes"]).strip())
    except (ValueError, TypeError):
        out["cooldown_minutes"] = _DEFAULT_POLICY["cooldown_minutes"]
    return out


def _meets_min_severity(severity: str, min_severity: str) -> bool:
    sev = _SEVERITY_ORDER.get((severity or "info").strip().lower(), 0)
    floor = _SEVERITY_ORDER.get((min_severity or "warn").strip().lower(), 1)
    return sev >= floor


def _effective_channel(policy: dict[str, Any]) -> str:
    """Resolve the channel actually deliverable in Phase 1: the policy's
    delivery if implemented, else its fallback, else log_only."""
    delivery = str(policy.get("delivery", "log_only")).strip().lower()
    if delivery in _IMPLEMENTED_CHANNELS:
        return delivery
    fallback = str(policy.get("fallback", "log_only")).strip().lower()
    if fallback in _IMPLEMENTED_CHANNELS:
        return fallback
    return "log_only"


async def _recently_delivered(
    pool: Any, *, kind: str, dedup_key: Optional[str], cooldown_minutes: int
) -> bool:
    """True if the same (kind, dedup_key) was actually delivered within the
    cooldown window. No dedup_key → never suppressed (each finding stands
    alone)."""
    if not dedup_key or cooldown_minutes <= 0:
        return False
    from datetime import timedelta

    cutoff = _now() - timedelta(minutes=cooldown_minutes)
    try:
        row = await pool.fetchrow(_RECENT_DELIVERY_SQL, kind, dedup_key, cutoff)
        return row is not None
    except Exception as e:  # noqa: BLE001
        logger.debug("[findings_dispatcher] cooldown check failed (%s)", e)
        return False


async def _mark(
    pool: Any,
    *,
    finding_id: int,
    kind: str,
    dedup_key: Optional[str],
    channel: str,
    result: str,
) -> None:
    try:
        await pool.execute(
            _MARK_SQL, finding_id, kind, dedup_key, channel, result, _now()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[findings_dispatcher] failed to mark finding %s (%s) — it will "
            "be re-polled next cycle", finding_id, e,
        )


def _parse_details(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return {}


async def poll_and_dispatch(
    pool: Any,
    *,
    batch_size: int = 50,
    notify_fn: Optional[Any] = None,
) -> dict[str, int]:
    """Poll undelivered findings and route each per its per-kind policy.

    Returns ``{'polled', 'sent', 'log_only', 'suppressed', 'errors'}``.
    Best-effort: a poll-level failure logs + returns zeros; per-finding
    failures are marked on the state row and counted, and the loop
    continues. Every polled finding gets a state row (even suppressed /
    log_only / errored) so it is never re-polled into a spam loop.
    """
    summary = {"polled": 0, "sent": 0, "log_only": 0, "suppressed": 0, "errors": 0}

    try:
        rows = await pool.fetch(_POLL_SQL, batch_size)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "findings_dispatch_state" in msg or "does not exist" in msg.lower():
            logger.warning(
                "[findings_dispatcher] poll failed (likely seed migration "
                "pending): %s", msg,
            )
        else:
            logger.warning("[findings_dispatcher] poll failed: %s", msg)
        return summary

    if not rows:
        return summary
    summary["polled"] = len(rows)

    policies_raw = await _load_policies(pool)

    if notify_fn is None:
        notify_fn = await _resolve_notify_fn(pool)

    for row in rows:
        finding_id = row["id"]
        details = _parse_details(row["details"])
        kind = (details.get("kind") or "unknown").strip() or "unknown"
        dedup_key = details.get("dedup_key")
        # Finding severity: prefer the audit_log column, fall back to details.
        severity = row["severity"] or details.get("severity") or "info"
        title = details.get("title") or kind
        body = details.get("body") or ""

        policy = _resolve_policy(kind, policies_raw)
        channel = _effective_channel(policy)

        # min_severity gate: anything below the floor is logged, not pushed.
        if channel in ("discord", "telegram") and not _meets_min_severity(
            severity, str(policy.get("min_severity", "warn"))
        ):
            channel = "log_only"

        # Cooldown: same dedup_key delivered recently → suppress this fire.
        if channel in ("discord", "telegram") and await _recently_delivered(
            pool, kind=kind, dedup_key=dedup_key,
            cooldown_minutes=int(policy["cooldown_minutes"]),
        ):
            await _mark(
                pool, finding_id=finding_id, kind=kind, dedup_key=dedup_key,
                channel=channel, result="suppressed_cooldown",
            )
            summary["suppressed"] += 1
            continue

        if channel == "log_only":
            await _mark(
                pool, finding_id=finding_id, kind=kind, dedup_key=dedup_key,
                channel="log_only", result="log_only",
            )
            summary["log_only"] += 1
            continue

        # discord / telegram delivery.
        if notify_fn is None:
            await _mark(
                pool, finding_id=finding_id, kind=kind, dedup_key=dedup_key,
                channel=channel, result="error: no notify channel reachable",
            )
            summary["errors"] += 1
            continue

        message = f"[{str(severity).upper()}] {title}"
        if body:
            message += f"\n{body}"
        critical = channel == "telegram"
        try:
            result = notify_fn(message, critical=critical)
            if hasattr(result, "__await__"):
                await result
            await _mark(
                pool, finding_id=finding_id, kind=kind, dedup_key=dedup_key,
                channel=channel, result="sent",
            )
            summary["sent"] += 1
        except Exception as e:  # noqa: BLE001
            await _mark(
                pool, finding_id=finding_id, kind=kind, dedup_key=dedup_key,
                channel=channel, result=f"error: {str(e)[:160]}",
            )
            summary["errors"] += 1
            logger.warning(
                "[findings_dispatcher] delivery failed for finding %s "
                "(%s/%s): %s", finding_id, kind, channel, e,
            )

    if summary["sent"] or summary["errors"] or summary["suppressed"]:
        logger.info(
            "[findings_dispatcher] cycle: polled=%d sent=%d log_only=%d "
            "suppressed=%d errors=%d",
            summary["polled"], summary["sent"], summary["log_only"],
            summary["suppressed"], summary["errors"],
        )
    return summary


async def _resolve_notify_fn(pool: Any) -> Optional[Any]:
    """Reuse alert_dispatcher's worker→brain notify resolution so findings
    and alerts share one delivery path. Returns an adapter with signature
    ``(message, *, critical=False)`` or None if no channel is reachable."""
    try:
        from alert_dispatcher import _resolve_notify_fn as _resolve
    except ImportError:
        try:
            from brain.alert_dispatcher import _resolve_notify_fn as _resolve
        except ImportError:
            logger.debug("[findings_dispatcher] alert_dispatcher notify resolver unavailable")
            return None
    try:
        return await _resolve(pool=pool)
    except Exception as e:  # noqa: BLE001
        logger.debug("[findings_dispatcher] notify resolve failed (%s)", e)
        return None
