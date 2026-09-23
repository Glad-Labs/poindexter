"""SyncResendDeliveryEventsJob — record what happened to the mail we sent.

``campaign_email_logs`` says we handed a message to Resend. It cannot say
whether the message landed, bounced, or drew a complaint — that is what
``subscriber_events`` is for, and its only producer was a webhook route
unreachable from the internet. Receipts stopped 2026-07-19 while sending
stayed healthy, and nothing alerted because the freshness threshold had
been raised 7 -> 180 days.

This polls ``GET /emails`` instead, which carries ``last_event`` per
message. No ingress, same reasoning as the Lemon Squeezy invoice poll.

Runs hourly: delivery state settles in seconds-to-minutes and a missed
tick costs nothing, because the poll re-reads the whole recent window and
idempotency is enforced by a unique index rather than by a cursor.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)


class SyncResendDeliveryEventsJob:
    name = "sync_resend_delivery"
    description = "Poll Resend for per-message delivery state into subscriber_events"
    schedule = "every 1 hour"
    # Writes are ON CONFLICT DO NOTHING against a unique index, so two
    # overlapping passes converge on the same rows without double-writing.
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if site_config.get("newsletter_enabled", "false").lower() not in (
            "true",
            "1",
            "yes",
        ):
            return JobResult(ok=True, detail="newsletter_enabled=false — no-op")

        from poindexter.services.resend_delivery import poll_delivery_state

        try:
            outcome = await poll_delivery_state(pool, site_config)
        except Exception as exc:
            logger.error(
                "[SyncResendDeliveryEventsJob] poll failed: %s",
                describe_exception(exc), exc_info=True,
            )
            emit_finding(
                source="resend_delivery",
                kind="resend_delivery_poll_failed",
                title="Resend delivery poll failed",
                body=describe_exception(exc),
                severity="warn",
                dedup_key="resend_delivery_poll_failed",
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        if outcome.errors:
            emit_finding(
                source="resend_delivery",
                kind="resend_delivery_poll_failed",
                title=f"Resend delivery poll hit {len(outcome.errors)} error(s)",
                body="\n".join(outcome.errors[:5]),
                severity="warn",
                dedup_key="resend_delivery_poll_errors",
            )

        detail = (
            f"{outcome.emails_seen} email(s) seen, "
            f"{outcome.rows_written} row(s) written"
        )
        logger.info("[SyncResendDeliveryEventsJob] %s", detail)
        return JobResult(
            ok=not outcome.errors,
            detail=detail,
            changes_made=outcome.rows_written,
            metrics=outcome.as_metrics(),
        )
