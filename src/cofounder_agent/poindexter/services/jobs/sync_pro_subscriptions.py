"""SyncProSubscriptionsJob — poll Lemon Squeezy, reconcile Pro repo access.

The delivery half of the pay→deliver chain (glad-labs-stack#3216). Every
tick it asks the Lemon Squeezy API for the current subscription set and
converges GitHub collaborator access on the Pro repo to the access policy
in ``services/pro_delivery.py`` — invite on purchase, revoke on expiry.

Polling (not webhooks) is deliberate: the worker has no public ingress on
a local-first install, and a poll reconciles after downtime where a missed
webhook is lost forever. Latency is one tick (~5 min), which the buyer
experiences as "the GitHub invite email arrived right after the receipt."

No-op until ``pro_delivery_enabled=true``. When enabled but unconfigured
(missing repo / PAT / LS key) it fails LOUD every tick — an operator who
flipped the switch believes delivery is running, so silence would be the
worst outcome (feedback_no_silent_defaults).

**A failed poll is deferred, not paged (2026-10-23).** Each Lemon Squeezy
blip (a connect timeout, a connection refused, a 503) paged twice, once as
``pro_delivery_error`` and once as the scheduler's ``job_failure``, and the
next poll five minutes later succeeded: three such pairs in three weeks, with
no subscriber affected, because a poll reconciles whatever it missed. A
network or provider-side failure now defers to the next tick. It pages once,
as ``pro_delivery_error``, only when no sync has succeeded for
``pro_delivery_outage_alert_minutes`` (60): a buyer waiting that long for
their invite is the real fault. The last good sync is the scheduler's own
``job_run`` record for this job. A configuration or authorization error still
pages on the first failure.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

_OUTAGE_ALERT_MINUTES_KEY = "pro_delivery_outage_alert_minutes"


def _is_transient(exc: BaseException) -> bool:
    """A failure the next poll can recover from: the network, or the provider
    being down. A 4xx (bad key, revoked token) is not."""
    from poindexter.services.net_transient import is_transient_network_error

    if is_transient_network_error(exc):
        return True
    if type(exc).__name__ in ("ReadTimeout", "WriteTimeout", "PoolTimeout", "RemoteProtocolError"):
        return True
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return isinstance(status, int) and status >= 500


async def _minutes_since_last_good_sync(pool: Any) -> float | None:
    """From the scheduler's job_run records; None if there is no good sync on
    record (or the read fails), which is treated as an outage."""
    try:
        value = await pool.fetchval(
            "SELECT EXTRACT(EPOCH FROM now() - max(timestamp)) / 60 FROM audit_log "
            "WHERE event_type = 'job_run' AND source = 'sync_pro_subscriptions' "
            "AND details->>'ok' = 'true'"
        )
    except Exception as exc:
        logger.warning("[SyncProSubscriptionsJob] last good sync unreadable: %s", describe_exception(exc))
        return None
    return float(value) if value is not None else None


class SyncProSubscriptionsJob:
    name = "sync_pro_subscriptions"
    description = (
        "Poll Lemon Squeezy subscriptions and reconcile GitHub Pro-repo access"
    )
    schedule = "every 5 minutes"
    # GitHub mutations + per-row stamps — two overlapping passes could
    # double-fire invites/revokes, so the scheduler must serialize runs.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if site_config.get("pro_delivery_enabled", "false").lower() not in (
            "true",
            "1",
            "yes",
        ):
            return JobResult(ok=True, detail="pro_delivery_enabled=false — no-op")

        from poindexter.services.pro_delivery import ProDeliveryConfigError, run_sync

        try:
            outcome = await run_sync(pool, site_config)
        except ProDeliveryConfigError as exc:
            emit_finding(
                source="pro_delivery",
                kind="pro_delivery_error",
                title="Pro delivery is enabled but not configured",
                body=describe_exception(exc),
                severity="error",
                dedup_key="pro_delivery_misconfigured",
            )
            return JobResult(ok=False, detail=describe_exception(exc))
        except Exception as exc:
            if _is_transient(exc):
                return await self._defer(pool, site_config, exc)
            logger.error(
                "[SyncProSubscriptionsJob] sync pass failed: %s", describe_exception(exc), exc_info=True
            )
            emit_finding(
                source="pro_delivery",
                kind="pro_delivery_error",
                title="Pro delivery sync pass failed",
                body=describe_exception(exc),
                severity="warn",
                dedup_key="pro_delivery_sync_failed",
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        changes = len(outcome.invited) + len(outcome.revoked) + outcome.revenue_rows
        detail = (
            f"{outcome.subscriptions_seen} subscription(s); "
            f"invited {len(outcome.invited)}, revoked {len(outcome.revoked)}, "
            f"missing_username {len(outcome.missing_username)}, "
            f"revenue_rows {outcome.revenue_rows}, errors {len(outcome.errors)}"
        )
        if changes:
            logger.info("[SyncProSubscriptionsJob] %s", detail)
        return JobResult(
            ok=not outcome.errors,
            detail=detail,
            changes_made=changes,
            metrics=outcome.as_metrics(),
        )

    async def _defer(self, pool: Any, site_config: Any, exc: BaseException) -> JobResult:
        """A network or provider-side failure: try again next tick, and page
        only when syncing has been down for longer than the outage window."""
        try:
            limit = max(1, int(site_config.get(_OUTAGE_ALERT_MINUTES_KEY, "60") or 60))
        except (TypeError, ValueError):
            limit = 60
        since = await _minutes_since_last_good_sync(pool)
        reason = describe_exception(exc)
        if since is not None and since < limit:
            logger.warning(
                "[SyncProSubscriptionsJob] Lemon Squeezy unavailable (%s); last good sync "
                "%.0f min ago, retrying next tick", reason, since,
            )
            return JobResult(ok=True, detail=f"deferred: {reason}", changes_made=0)
        when = "no successful sync on record" if since is None else f"last good sync {since:.0f} minutes ago"
        emit_finding(
            source="pro_delivery",
            kind="pro_delivery_error",
            title="Pro delivery can't reach Lemon Squeezy",
            body=(
                f"Every poll is failing ({when}; alert after {limit} min). New Pro "
                f"buyers won't get their GitHub invite until it recovers. Latest "
                f"error: {reason}"
            ),
            severity="warn",
            dedup_key="pro_delivery_sync_stalled",
        )
        return JobResult(ok=True, detail=f"deferred, outage alerted: {reason}", changes_made=0)
