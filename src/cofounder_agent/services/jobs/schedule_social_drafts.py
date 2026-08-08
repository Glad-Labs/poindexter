"""ScheduleSocialDraftsJob — drive the local social-post schedule queue.

Two passes per run, in this order:

1. **Auto-slot** (``auto_schedule_ready_drafts``) — pending drafts whose blog
   post has gone live get a fire time from the per-platform drip offsets.
   No-op unless ``social_schedule_enabled=true`` AND
   ``social_schedule_offsets`` names the platform.
2. **Fire** (``fire_due_drafts``) — scheduled drafts whose slot has arrived go
   through ``approve_draft``, which re-runs the publish gate and URL repair at
   the moment of posting.

Auto-slot runs first so a draft slotted for "publish+0m" fires on the same
sweep rather than waiting a minute for the next one.

Why the queue is ours and not Postiz's: ``approve_draft`` refuses to post
while the promoted post isn't live. Handing Postiz a future-dated post would
run that check at schedule time, hours early — a post whose publish slipped
or that got pulled would still promote itself, at a URL that 404s. Holding
the queue here also means cancel/reschedule stay in the CLI and console
instead of the Postiz UI, which was the point.

Config (``plugin.job.schedule_social_drafts``):
- ``config.fire_limit`` — override ``social_schedule_fire_batch_size``

The job is a no-op when ``social_drafts_enabled=false``. Note the fire pass
is NOT gated on ``social_schedule_enabled``: that switch governs *automatic*
slotting only, so a draft an operator scheduled by hand still goes out with
auto-drip off.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)


class ScheduleSocialDraftsJob:
    name = "schedule_social_drafts"
    description = (
        "Auto-slot social drafts for published posts and fire the ones whose "
        "scheduled time has arrived"
    )
    # Minute cadence: the queue's resolution is this interval, so a slot is
    # honoured to within a minute. Matches scheduled_publisher's poll.
    schedule = "every 1 minute"
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

        from services.social_drafts import SocialDraftsService

        svc = SocialDraftsService()

        try:
            auto = await svc.auto_schedule_ready_drafts(pool, site_config)
        except Exception as exc:
            # A broken auto-slot pass must not block firing drafts that are
            # already queued — those are a live commitment with a time on them.
            logger.error(
                "[ScheduleSocialDraftsJob] auto-schedule pass raised: %s", exc
            )
            auto = {"scheduled": 0, "detail": f"auto-schedule failed: {exc}"}

        fire_limit = config.get("fire_limit")
        try:
            fired = await svc.fire_due_drafts(
                pool,
                site_config,
                limit=int(fire_limit) if fire_limit is not None else None,
            )
        except Exception as exc:
            logger.error("[ScheduleSocialDraftsJob] fire pass raised: %s", exc)
            return JobResult(
                ok=False,
                detail=f"auto-scheduled {auto['scheduled']}; fire failed: {exc}",
            )

        detail = (
            f"auto-scheduled {auto['scheduled']}; due {fired['due']} → "
            f"posted {fired['posted']}, blocked {fired['blocked']}, "
            f"failed {fired['failed']}, overdue {fired['overdue']}"
        )
        if auto["scheduled"] or fired["due"]:
            logger.info("[ScheduleSocialDraftsJob] %s", detail)

        return JobResult(
            ok=True,
            detail=detail,
            # Rows actually moved: slots assigned plus promos sent.
            changes_made=auto["scheduled"] + fired["posted"],
            metrics={
                "auto_scheduled": auto["scheduled"],
                "due": fired["due"],
                "posted": fired["posted"],
                "blocked": fired["blocked"],
                "failed": fired["failed"],
                "overdue": fired["overdue"],
            },
        )
