"""BackfillMissingSocialDraftsJob — reconcile published posts with incomplete
social draft coverage.

social.generate_drafts (the atom) can fail mid-generation without a trace —
a transient LLM error is caught and logged, not re-raised, so the post still
publishes but zero social_post_drafts rows land for one or more platforms
(poindexter#863). RetryFailedSocialDraftsJob can't help: it only retries
EXISTING 'failed' rows, and there is nothing to retry when no row was ever
written.

This job periodically calls SocialDraftsService.reconcile_missing_drafts,
which re-invokes the atom's own run() for each recently-published post whose
task's template includes social.generate_drafts. Safe to call repeatedly —
the atom's existing_draft_keys() idempotency guard skips any platform that
already has a pending/failed/posted draft, so a post with full coverage
costs one cheap query and zero LLM calls; only genuinely missing platforms
trigger real (re)generation.

Config (``plugin.job.backfill_missing_social_drafts``):
- ``config.lookback_days`` override for ``social_draft_backfill_lookback_days``

The job is a no-op when ``social_drafts_enabled=false``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.integrations.operator_notify import notify_operator
from services.site_config import SiteConfig
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class BackfillMissingSocialDraftsJob:
    name = "backfill_missing_social_drafts"
    description = (
        "Regenerate social drafts for published posts with incomplete "
        "platform coverage"
    )
    schedule = "every 6 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if site_config.get("social_drafts_enabled", "false").lower() not in (
            "true",
            "1",
            "yes",
        ):
            return JobResult(ok=True, detail="social_drafts_enabled=false — no-op")

        lookback_days = int(
            config.get(
                "lookback_days",
                site_config.get("social_draft_backfill_lookback_days", "14"),
            )
        )

        from services.social_drafts import SocialDraftsService

        try:
            result = await SocialDraftsService().reconcile_missing_drafts(
                pool, site_config, lookback_days
            )
        except Exception as exc:
            logger.error("[BackfillMissingSocialDraftsJob] reconcile failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=describe_exception(exc))

        if result["errors"]:
            await notify_operator(
                f"[social] backfill job hit {len(result['errors'])} "
                "reconciliation error(s) — check worker logs",
                critical=False,
                site_config=site_config,
            )

        msg = (
            f"checked {result['candidates_checked']} published post(s), "
            f"created {result['drafts_created']} draft(s), "
            f"{len(result['errors'])} error(s)"
        )
        return JobResult(
            ok=True,
            detail=msg,
            changes_made=result["drafts_created"],
        )
