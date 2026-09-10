"""SyncPostizDeliveryStateJob — reconcile 'posted' drafts against Postiz reality.

``approve_draft`` marks a draft ``posted`` when Postiz ACCEPTS the enqueue
(HTTP 200), but the actual platform publish runs async on Postiz's Temporal
workers and can still fail — the 2026-08-26 incident: X returned 402
"credits depleted" for five straight tweets while our side showed all five
as ``posted``, so nothing alerted and the gap was only found by a manual
Postiz-DB audit. This job closes the loop:

- Drafts ``posted`` within the lookback window are matched (by
  ``postiz_post_id``) against ``GET /public/v1/posts``.
- Postiz state ``ERROR`` → the draft is demoted to ``failed`` (posted_at
  cleared, error recorded) and a ``social_post_delivery_failed`` finding is
  emitted (→ Discord per the findings policy). The hourly
  ``RetryFailedSocialDraftsJob`` then owns retries, bounded by
  ``social_draft_max_retries`` as usual.
- Postiz state ``PUBLISHED`` → the platform permalink and state are stamped
  into ``platform_config`` (``postiz_delivery_state`` / ``release_url``) so
  the row is provably delivered and never re-checked.
- ``QUEUE``/``DRAFT`` (still in flight) and ids missing from the window are
  left alone for the next pass.

Config (``plugin.job.sync_postiz_delivery_state``):
- ``config.lookback_days`` (default 7) — how far back to re-verify.

The job is a no-op when ``social_drafts_enabled=false``. An unreachable
Postiz is a FAILED run (ok=False), never a quiet "0 errors" — a delivery
auditor that can't reach its source has nothing truthful to report.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from services.site_config import SiteConfig
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Terminal Postiz states this job acts on; anything else is "in flight".
_STATE_ERROR = "ERROR"
_STATE_PUBLISHED = "PUBLISHED"


class SyncPostizDeliveryStateJob:
    name = "sync_postiz_delivery_state"
    description = "Reconcile posted social drafts against Postiz platform delivery state"
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config: SiteConfig | None = config.get("_site_config")
        if site_config is None:
            return JobResult(ok=False, detail="no _site_config in config — skipping")

        if site_config.get("social_drafts_enabled", "false").lower() not in (
            "true", "1", "yes"
        ):
            return JobResult(ok=True, detail="social_drafts_enabled=false — no-op")

        lookback_days = int(config.get("lookback_days", 7))

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, platform, postiz_post_id, posted_at
                    FROM social_post_drafts
                    WHERE status = 'posted'
                      AND postiz_post_id IS NOT NULL
                      AND posted_at > now() - make_interval(days => $1)
                      AND COALESCE(platform_config->>'postiz_delivery_state', '')
                          <> $2
                    ORDER BY posted_at ASC
                    """,
                    lookback_days,
                    _STATE_PUBLISHED,
                )
        except Exception as exc:
            logger.error("[SyncPostizDeliveryState] DB query failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=describe_exception(exc))

        if not rows:
            return JobResult(ok=True, detail="no unverified posted drafts in window")

        from services.integrations.postiz_client import PostizClient

        base_url = site_config.get("postiz_api_url", "http://postiz:3000")
        api_key = await site_config.get_secret("postiz_api_key", "")
        client = PostizClient(base_url=base_url, api_key=api_key)

        # Window the Postiz query around the drafts we actually hold —
        # publishDate ≈ posted_at for "now" posts, padded a day each side.
        oldest = min(r["posted_at"] for r in rows)
        start = (oldest - timedelta(days=1)).astimezone(timezone.utc)
        end = datetime.now(timezone.utc) + timedelta(days=1)
        iso = "%Y-%m-%dT%H:%M:%SZ"
        try:
            postiz_posts = await client.list_posts(
                start.strftime(iso), end.strftime(iso)
            )
        except Exception as exc:
            logger.error("[SyncPostizDeliveryState] Postiz list failed: %s", describe_exception(exc))
            return JobResult(ok=False, detail=f"Postiz unreachable: {describe_exception(exc)}")

        by_id: dict[str, dict[str, Any]] = {
            str(p.get("id")): p for p in postiz_posts if p.get("id")
        }

        published = errored = in_flight = unknown = 0
        for row in rows:
            draft_id = str(row["id"])
            postiz_id = str(row["postiz_post_id"])
            remote = by_id.get(postiz_id)
            if remote is None:
                # Not in the window (deleted in Postiz, or clock edge) —
                # leave for the next pass rather than guess.
                unknown += 1
                continue
            state = str(remote.get("state", ""))
            if state == _STATE_PUBLISHED:
                published += 1
                stamp = {
                    "postiz_delivery_state": _STATE_PUBLISHED,
                    "release_url": str(remote.get("releaseURL") or ""),
                }
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE social_post_drafts
                        SET platform_config = platform_config || $2::jsonb
                        WHERE id = $1
                        """,
                        draft_id,
                        json.dumps(stamp),
                    )
            elif state == _STATE_ERROR:
                errored += 1
                err = (
                    f"Postiz delivery ERROR for post {postiz_id} "
                    f"({row['platform']}) — Postiz accepted the enqueue but the "
                    f"platform publish failed. Check the Postiz UI/Errors table "
                    f"for the platform response."
                )
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE social_post_drafts
                        SET status = 'failed',
                            posted_at = NULL,
                            error = $2,
                            platform_config = platform_config || $3::jsonb
                        WHERE id = $1 AND status = 'posted'
                        """,
                        draft_id,
                        err,
                        json.dumps({"postiz_delivery_state": _STATE_ERROR}),
                    )
                emit_finding(
                    source=self.name,
                    kind="social_post_delivery_failed",
                    title=f"{row['platform']} promo failed platform-side (draft {draft_id[:8]})",
                    body=err,
                    severity="warn",
                    dedup_key=f"social-delivery:{draft_id}:{postiz_id}",
                    extra={"draft_id": draft_id, "platform": row["platform"],
                           "postiz_post_id": postiz_id},
                )
                logger.warning(
                    "[SyncPostizDeliveryState] demoted draft %s (%s) — Postiz "
                    "post %s state=ERROR",
                    draft_id[:8], row["platform"], postiz_id,
                )
            else:
                in_flight += 1

        detail = (
            f"checked {len(rows)}: {published} published, {errored} demoted to "
            f"failed, {in_flight} in flight, {unknown} unknown"
        )
        return JobResult(
            ok=True,
            detail=detail,
            changes_made=published + errored,
            metrics={
                "checked": len(rows),
                "published": published,
                "errored": errored,
                "in_flight": in_flight,
                "unknown": unknown,
            },
        )
