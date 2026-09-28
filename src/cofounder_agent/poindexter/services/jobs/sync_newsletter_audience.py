"""SyncNewsletterAudienceJob — pull public signups into newsletter_subscribers.

The public signup form captures into a Resend segment, because the worker has
no public ingress for the site to write to. This job is the owned copy's
producer: it lists the segment outbound-only and inserts new subscribers
(fresh unsubscribe token, verified on signup), and it applies opt-outs made
on the Resend side. It never re-subscribes an address the owned table has
unsubscribed. See :mod:`poindexter.services.newsletter_audience` for why.

Every 15 minutes. A new subscriber must be on the list before the next
publish mails it, and posts go out several times a day. A missed tick costs
nothing: the pull re-reads the whole segment and every write is idempotent.

No-op until ``resend_audience_id`` is set, which is the case on a fresh
install.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)


class SyncNewsletterAudienceJob:
    name = "sync_newsletter_audience"
    description = (
        "Pull newsletter signups from the Resend segment into "
        "newsletter_subscribers"
    )
    schedule = "every 15 minutes"
    # Read-then-insert per contact. Two overlapping passes would race on the
    # same address, so the scheduler must serialize them.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        from poindexter.services.newsletter_audience import (
            AudienceConfigError,
            resolve_segment_id,
            sync_segment_to_subscribers,
        )

        try:
            if resolve_segment_id(site_config) is None:
                return JobResult(ok=True, detail="resend_audience_id not set — no-op")
            outcome = await sync_segment_to_subscribers(pool, site_config)
        except AudienceConfigError as exc:
            return self._failed(
                describe_exception(exc), dedup_key="newsletter_audience_sync_config"
            )
        except Exception as exc:
            logger.error(
                "[SyncNewsletterAudienceJob] pull failed: %s",
                describe_exception(exc), exc_info=True,
            )
            return self._failed(
                describe_exception(exc), dedup_key="newsletter_audience_sync_failed"
            )

        if outcome.errors:
            emit_finding(
                source="newsletter_audience",
                kind="newsletter_audience_sync_failed",
                title=(
                    f"Newsletter signup sync hit {len(outcome.errors)} error(s)"
                ),
                body="\n".join(outcome.errors[:5]),
                severity="warn",
                dedup_key="newsletter_audience_sync_errors",
            )

        detail = outcome.summary()
        if outcome.changes:
            logger.info("[SyncNewsletterAudienceJob] %s", detail)
        return JobResult(
            ok=not outcome.errors,
            detail=detail,
            changes_made=outcome.changes,
            metrics=outcome.as_metrics(),
        )

    @staticmethod
    def _failed(cause: str, *, dedup_key: str) -> JobResult:
        emit_finding(
            source="newsletter_audience",
            kind="newsletter_audience_sync_failed",
            title="Newsletter signup sync failed",
            body=(
                f"{cause}\n\nNew signups are still captured in the Resend "
                "segment; they are not reaching newsletter_subscribers, so "
                "they will not be mailed until this clears."
            ),
            severity="warn",
            dedup_key=dedup_key,
        )
        return JobResult(ok=False, detail=cause)
