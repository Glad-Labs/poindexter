"""ApplyUnsubscribeRequestsJob — drain the edge unsubscribe queue.

The unsubscribe relay (a Cloudflare Worker) takes the recipient's click and
parks the token in KV; this job applies it to ``newsletter_subscribers``.
Ingress genuinely IS required for the click itself — a human in a mail
client must reach something live — but the DB write stays outbound-only,
same as every other integration here.

Every 5 minutes. An opt-out that takes a few minutes to apply is fine
(CAN-SPAM allows 10 business days); one that is never applied is not, which
is why the relay holds tokens for RETENTION_DAYS and this job acks only
after the write lands.

No-op until ``newsletter_unsubscribe_relay_url`` is set.
"""

from __future__ import annotations

import logging
from typing import Any

from poindexter.plugins.job import JobResult
from poindexter.services.site_config import SiteConfig
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)


class ApplyUnsubscribeRequestsJob:
    name = "apply_unsubscribe_requests"
    description = "Apply newsletter unsubscribe requests queued at the edge relay"
    schedule = "every 5 minutes"
    # Applies opt-outs and acks them. Two overlapping passes could ack a
    # token the other is still writing, so the scheduler must serialize.
    idempotent = False

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if not (site_config.get("newsletter_unsubscribe_relay_url", "") or "").strip():
            return JobResult(ok=True, detail="no relay configured — no-op")

        from poindexter.services.unsubscribe_relay import drain_unsubscribe_queue

        try:
            outcome = await drain_unsubscribe_queue(pool, site_config)
        except Exception as exc:
            logger.error(
                "[ApplyUnsubscribeRequestsJob] drain failed: %s",
                describe_exception(exc), exc_info=True,
            )
            emit_finding(
                source="unsubscribe_relay",
                kind="unsubscribe_relay_poll_failed",
                title="Unsubscribe relay drain failed",
                body=describe_exception(exc),
                severity="warn",
                dedup_key="unsubscribe_relay_poll_failed",
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        if outcome.errors:
            emit_finding(
                source="unsubscribe_relay",
                kind="unsubscribe_relay_poll_failed",
                title=f"Unsubscribe relay drain hit {len(outcome.errors)} error(s)",
                body="\n".join(outcome.errors[:5]),
                severity="warn",
                dedup_key="unsubscribe_relay_drain_errors",
            )

        detail = (
            f"{outcome.pending_seen} queued, "
            f"{outcome.unsubscribed} applied, {outcome.acked} acked"
        )
        return JobResult(
            ok=not outcome.errors,
            detail=detail,
            changes_made=outcome.unsubscribed,
            metrics=outcome.as_metrics(),
        )
