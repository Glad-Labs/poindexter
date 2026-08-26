"""CancelOrphanedSocialDraftsJob — cancel social drafts for rejected content.

Social post drafts are generated speculatively at pipeline finalize — before
operator sign-off (a QA-flagged post rides through to awaiting_approval) — so a
post that is later rejected leaves its promos stranded in ``pending``,
cluttering the action inbox. This reaper cancels pending/failed drafts whose
content task reached a terminal-reject status, path-independently: it keys off
the task status (not one reject entry point), so it catches every rejection
path AND back-fills historical orphans on first run. Already-``posted`` drafts
are left as-is — retracting a live social post is a separate concern.

The publish-gate in ``SocialDraftsService.approve_draft`` already prevents an
orphan from actually posting (it refuses unless the promoted post is live), so
this is queue hygiene rather than a posting-safety fix; the two are
complementary.

The job is a no-op when ``social_drafts_enabled=false``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.site_config import SiteConfig
from utils.exception_format import describe_exception

logger = logging.getLogger(__name__)


class CancelOrphanedSocialDraftsJob:
    name = "cancel_orphaned_social_drafts"
    description = "Cancel pending social drafts whose content task was rejected"
    schedule = "every 15 minutes"
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

        from services.social_drafts import SocialDraftsService

        try:
            cancelled = await SocialDraftsService().cancel_orphaned_for_rejected_tasks(
                pool
            )
        except Exception as exc:
            logger.error(
                "[CancelOrphanedSocialDraftsJob] cancel query failed: %s", describe_exception(exc)
            )
            return JobResult(ok=False, detail=describe_exception(exc))

        if cancelled:
            logger.info(
                "[CancelOrphanedSocialDraftsJob] cancelled %d orphaned draft(s) "
                "for rejected content",
                cancelled,
            )
        return JobResult(ok=True, detail=f"cancelled {cancelled} orphaned draft(s)")
