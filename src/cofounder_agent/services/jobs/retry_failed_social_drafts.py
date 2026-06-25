"""RetryFailedSocialDraftsJob — retry social_post_drafts rows stuck at 'failed'.

Picks up to ``batch_size`` rows where status='failed' AND retry_count is
below the ``social_draft_max_retries`` setting (default 3) and calls
``SocialDraftsService.retry_draft`` on each, which increments retry_count,
resets the row to 'pending', then calls ``approve_draft`` immediately.

Config (``plugin.job.retry_failed_social_drafts``):
- ``config.batch_size`` (default 10) — max drafts to retry per run

The job is a no-op when ``social_drafts_enabled=false``.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


class RetryFailedSocialDraftsJob:
    name = "retry_failed_social_drafts"
    description = "Retry social post drafts that failed posting via Postiz"
    schedule = "every 1 hour"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False, detail="no _site_config in config — skipping"
            )

        if site_config.get("social_drafts_enabled", "false").lower() not in (
            "true", "1", "yes"
        ):
            return JobResult(ok=True, detail="social_drafts_enabled=false — no-op")

        max_retries = int(site_config.get("social_draft_max_retries", "3"))
        batch_size = int(config.get("batch_size", 10))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, platform
                    FROM social_post_drafts
                    WHERE status = 'failed'
                      AND retry_count < $1
                    ORDER BY created_at ASC
                    LIMIT $2
                    """,
                    max_retries,
                    batch_size,
                )
        except Exception as exc:
            logger.error("[RetryFailedSocialDraftsJob] DB query failed: %s", exc)
            return JobResult(ok=False, detail=str(exc))

        if not rows:
            return JobResult(ok=True, detail="no retryable failed drafts")

        from services.social_drafts import SocialDraftsService

        svc = SocialDraftsService()
        succeeded = 0
        failed = 0
        for row in rows:
            draft_id = str(row["id"])
            platform = row["platform"]
            try:
                result = await svc.retry_draft(draft_id, pool, site_config)
                if result.get("success"):
                    succeeded += 1
                    logger.info(
                        "[RetryFailedSocialDraftsJob] retried draft %s (%s) → posted",
                        draft_id[:8], platform,
                    )
                else:
                    failed += 1
                    logger.warning(
                        "[RetryFailedSocialDraftsJob] retry %s (%s) still failed: %s",
                        draft_id[:8], platform, result.get("error"),
                    )
            except Exception as exc:
                failed += 1
                logger.error(
                    "[RetryFailedSocialDraftsJob] retry %s raised: %s", draft_id[:8], exc,
                )

        msg = f"retried {len(rows)} drafts: {succeeded} posted, {failed} still failed"
        return JobResult(ok=True, detail=msg)
