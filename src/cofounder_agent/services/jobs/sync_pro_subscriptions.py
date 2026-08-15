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
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.site_config import SiteConfig
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


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

        from services.pro_delivery import ProDeliveryConfigError, run_sync

        try:
            outcome = await run_sync(pool, site_config)
        except ProDeliveryConfigError as exc:
            emit_finding(
                source="pro_delivery",
                kind="pro_delivery_error",
                title="Pro delivery is enabled but not configured",
                body=str(exc),
                severity="error",
                dedup_key="pro_delivery_misconfigured",
            )
            return JobResult(ok=False, detail=str(exc))
        except Exception as exc:
            logger.error(
                "[SyncProSubscriptionsJob] sync pass failed: %s", exc, exc_info=True
            )
            emit_finding(
                source="pro_delivery",
                kind="pro_delivery_error",
                title="Pro delivery sync pass failed",
                body=str(exc),
                severity="warn",
                dedup_key="pro_delivery_sync_failed",
            )
            return JobResult(ok=False, detail=str(exc))

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
