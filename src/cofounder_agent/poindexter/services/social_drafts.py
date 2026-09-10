"""SocialDraftsService — create, approve, reject, and query social post drafts.

All surfaces (CLI, API, MCP) delegate here. This module is the only place
that writes to social_post_drafts or calls PostizClient.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from services.distribution_ref import tag_for
from services.integrations.operator_notify import notify_operator
from services.integrations.postiz_client import PostizClient
from services.site_config import SiteConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics — module-level singletons, same pattern as social_poster.
# metrics_exporter.py imports SOCIAL_DRAFT_* for side-effect registration.
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    SOCIAL_DRAFT_CREATED_TOTAL = _Counter(
        "poindexter_social_draft_created_total",
        "Social post drafts created, by platform",
        ["platform"],
    )
    SOCIAL_DRAFT_POSTED_TOTAL = _Counter(
        "poindexter_social_draft_posted_total",
        "Social post drafts successfully posted via Postiz, by platform",
        ["platform"],
    )
    SOCIAL_DRAFT_FAILED_TOTAL = _Counter(
        "poindexter_social_draft_failed_total",
        "Social post draft posting failures, by platform",
        ["platform"],
    )
except Exception:  # pragma: no cover
    class _NoopCounter:  # type: ignore[no-redef]
        def labels(self, **_kwargs):  # noqa: D401
            return self
        def inc(self, _amount: float = 1.0) -> None:
            return None

    SOCIAL_DRAFT_CREATED_TOTAL = _NoopCounter()  # type: ignore[assignment]
    SOCIAL_DRAFT_POSTED_TOTAL = _NoopCounter()  # type: ignore[assignment]
    SOCIAL_DRAFT_FAILED_TOTAL = _NoopCounter()  # type: ignore[assignment]

# platform → Postiz __type
_PLATFORM_TYPE: dict[str, str] = {
    "twitter": "x",
    "linkedin": "linkedin",
    "mastodon": "mastodon",
    "bluesky": "bluesky",
    "reddit": "reddit",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
}

# platform → postiz_integration_id_* setting key
_INTEGRATION_KEY: dict[str, str] = {
    "twitter": "postiz_integration_id_twitter",
    "linkedin": "postiz_integration_id_linkedin",
    "mastodon": "postiz_integration_id_mastodon",
    "bluesky": "postiz_integration_id_bluesky",
    "reddit": "postiz_integration_id_reddit",
    "tiktok": "postiz_integration_id_tiktok",
    "instagram_reels": "postiz_integration_id_instagram",
}

# Task statuses where the content will never go live, so its still-pending
# social promos must be cancelled. Social drafts are generated speculatively at
# pipeline finalize (BEFORE operator sign-off — a QA-flagged post rides through
# to awaiting_approval), so a post rejected afterward strands its promos in
# 'pending' (visible in the action inbox). EXCLUDED (not terminal-dead):
# ``rejected_retry`` re-runs and refreshes its own drafts; ``approved`` /
# ``awaiting_approval`` are valid and awaiting publish.
#
# ``failed`` IS terminal: the stale-sweep only writes it once
# retry_count >= max_retries ("Exceeded maximum retries after stale sweep").
# It was missing from this tuple even though the reaper's docstring claimed
# "max-retry fail" was covered — a failed task's promos sat in 'pending' for
# 8 days (2026-08-20). Reaping it is safe: an operator retry moves the task
# back to 'pending', and a rejected-only key regenerates fresh copy on
# finalize (poindexter#833 idempotency), so nothing is lost.
_TERMINAL_REJECT_TASK_STATUSES: tuple[str, ...] = (
    "rejected_final",
    "rejected",
    "dismissed",
    "expired",
    "failed",
)

# Statuses where the (task, platform, subreddit) key is SPOKEN FOR — a promo
# for it exists or has gone out, so generating another would duplicate it.
# ``scheduled`` belongs here for the same reason ``pending`` does: the draft
# is a live commitment, just with a fire time attached. Leaving it out let a
# finalize re-run insert a second draft for a key that already had one, and
# both would post (poindexter#833). Mirrored by the partial unique index
# ``ux_social_post_drafts_active_key``, so change the two together.
#
# ``rejected`` is deliberately absent: rejection is per-copy, and a regen
# loop after a reject should offer fresh copy to review.
_KEY_HELD_STATUSES: tuple[str, ...] = ("pending", "scheduled", "failed", "posted")

# Statuses an operator decision can still move. ``approve_draft`` accepts all
# three: ``scheduled`` is here so the due-sweep can fire a slot through the
# same gate, and so a manual approve means "skip the wait, post it now".
_APPROVABLE_STATUSES: tuple[str, ...] = ("pending", "scheduled", "failed")

# Statuses that still need or await action, as opposed to tombstones. Drives
# the live-rows-first sort in ``list_drafts`` so the page cap can only ever
# hide history.
_LIVE_STATUSES: tuple[str, ...] = ("pending", "scheduled", "failed")

# Advisory-lock namespace for the approve/fire critical section. Two callers
# posting the SAME draft concurrently — the every-minute ``fire_due_drafts``
# sweep racing a manual ``poindexter social approve`` / console / MCP approve
# of a scheduled draft — both read an approvable status, both clear the gate,
# and both call Postiz, so the promo goes out TWICE (create_draft was hardened
# against the duplicate-promo class in poindexter#833; the approve path was
# not). ``pg_try_advisory_lock(NS, hashtext(draft_id))`` serializes the whole
# read→gate→Postiz→mark section cluster-wide (advisory locks span workers, so
# this holds for the SaaS multi-worker future too) on a dedicated pooled
# connection; the loser of the race bails WITHOUT posting. A stable arbitrary
# int32 chosen to not collide with GPU_ADVISORY_LOCK_KEY.
_SOCIAL_POST_LOCK_NS: int = 0x50AC  # "SoAC" — social approve critical section


@dataclass
class SocialDraftRow:
    id: str
    pipeline_task_id: str
    post_id: str | None
    platform: str
    content: str
    platform_config: dict[str, Any]
    status: str
    postiz_post_id: str | None
    error: str | None
    retry_count: int
    last_retry_at: datetime | None
    created_at: datetime
    approved_at: datetime | None
    posted_at: datetime | None
    post_status: str | None
    title: str | None = None
    resolved_post_id: str | None = None
    scheduled_at: datetime | None = None


@dataclass
class SocialDraftPage:
    """One window of ``list_drafts`` output plus the counts it was cut from.

    ``rows`` is capped by limit/offset, so it alone cannot answer "how many
    drafts are there?" — a consumer that counts statuses over ``rows`` reports
    the window, not the table. ``total`` (rows matching every filter) and
    ``status_counts`` (per-status totals for the post/task scope, ignoring the
    status filter) are what operator KPIs must read instead.
    """

    rows: list[SocialDraftRow]
    total: int
    status_counts: dict[str, int]


class SocialDraftsService:
    async def create_draft(
        self,
        pipeline_task_id: str,
        platform: str,
        content: str,
        platform_config: dict[str, Any],
        pool: Any,
    ) -> str:
        """Insert a pending draft, idempotent per (task, platform, subreddit).

        Finalize re-runs (preview_gate regen loops, checkpoint restore, task
        retry) call this again for keys that already have a draft; a bare
        INSERT stacked duplicates and the same promo got posted three times
        (poindexter#833). The guarded insert skips when the key is already
        held (``_KEY_HELD_STATUSES`` — pending/scheduled/failed/posted),
        returning the existing row's id, with the
        ``ux_social_post_drafts_active_key`` partial unique index as the ON
        CONFLICT race backstop. A ``rejected``-only key inserts fresh: an
        operator reject followed by a regen loop legitimately produces new
        copy to review.

        Returns the new draft UUID, or the existing draft's UUID when deduped.
        """
        subreddit_key = str((platform_config or {}).get("subreddit") or "")
        async with pool.acquire() as conn:
            new_id: str | None = await conn.fetchval(
                """
                INSERT INTO social_post_drafts
                    (pipeline_task_id, platform, content, platform_config)
                SELECT $1::text, $2::text, $3::text, $4::jsonb
                WHERE NOT EXISTS (
                    SELECT 1 FROM social_post_drafts
                    WHERE pipeline_task_id = $1
                      AND platform = $2
                      AND COALESCE(platform_config->>'subreddit', '') = $5
                      AND status = ANY($6::text[])
                )
                ON CONFLICT (
                    pipeline_task_id, platform,
                    (COALESCE(platform_config->>'subreddit', ''))
                ) WHERE status IN ('pending', 'scheduled', 'failed')
                DO NOTHING
                RETURNING id::text
                """,
                pipeline_task_id,
                platform,
                content,
                json.dumps(platform_config),
                subreddit_key,
                list(_KEY_HELD_STATUSES),
            )
            if new_id is None:
                existing_id: str | None = await conn.fetchval(
                    """
                    SELECT id::text FROM social_post_drafts
                    WHERE pipeline_task_id = $1
                      AND platform = $2
                      AND COALESCE(platform_config->>'subreddit', '') = $3
                      AND status = ANY($4::text[])
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    pipeline_task_id,
                    platform,
                    subreddit_key,
                    list(_KEY_HELD_STATUSES),
                )
                logger.info(
                    "[social_drafts] draft for task %s platform=%s%s already "
                    "exists (%s) — skipping duplicate create",
                    pipeline_task_id[:8],
                    platform,
                    f" subreddit={subreddit_key}" if subreddit_key else "",
                    (existing_id or "?")[:8],
                )
                return existing_id or ""
        SOCIAL_DRAFT_CREATED_TOTAL.labels(platform=platform).inc()
        return new_id

    async def existing_draft_keys(
        self, pipeline_task_id: str, pool: Any
    ) -> set[tuple[str, str]]:
        """(platform, subreddit-or-'') keys that already carry a draft.

        The generate atom consults this before spending LLM calls: keys held
        by ``_KEY_HELD_STATUSES`` are skipped on re-runs. ``rejected`` drafts
        don't count — rejection is per-copy, and a regen loop after a reject
        should offer fresh copy.
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT platform,
                       COALESCE(platform_config->>'subreddit', '') AS subreddit
                FROM social_post_drafts
                WHERE pipeline_task_id = $1
                  AND status = ANY($2::text[])
                """,
                pipeline_task_id,
                list(_KEY_HELD_STATUSES),
            )
        return {(r["platform"], r["subreddit"]) for r in rows}

    async def approve_draft(
        self,
        draft_id: str,
        pool: Any,
        site_config: SiteConfig,
    ) -> dict[str, Any]:
        """Approve a pending, scheduled, or failed draft: call Postiz now.

        Post-link gate (social-drafts linking bug): approving pushes to the
        platform NOW, so the blog post being promoted must already be live
        (``posts.status='published'``) and the URL in the copy is verified /
        repaired against the live posts row first. A blocked approve leaves
        the draft's status untouched — it is not a failure, so it neither
        consumes ``RetryFailedSocialDraftsJob`` retries nor needs a reset;
        approve again once the post is live.

        This is also the fire path for the local schedule queue: a
        ``scheduled`` draft whose slot arrives is approved through here by
        ``fire_due_drafts``, so the gate is re-checked at the moment of
        posting rather than when the operator picked the time. That is the
        whole reason the queue lives here instead of inside Postiz — a post
        whose publish slipped between scheduling and firing must not send a
        promo to a URL that 404s.

        **Single-flight per draft.** The whole read→gate→Postiz→mark section
        runs under a per-draft advisory lock (``_SOCIAL_POST_LOCK_NS``) so a
        due-sweep firing a ``scheduled`` draft and a concurrent manual approve
        of the same draft can't both reach ``create_post`` and double-post the
        promo. The loser of the race returns ``{"success": False,
        "contended": True}`` WITHOUT posting — the holder completes it.
        """
        async with pool.acquire() as lock_conn:
            # pg_try_advisory_lock returns exactly True/False on real Postgres;
            # a mock/degraded pool returns None (or a stub value), which we
            # treat as "no real lock backend — proceed" rather than as
            # contention (only a literal False means another session holds it).
            got = await lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1, hashtext($2))",
                _SOCIAL_POST_LOCK_NS,
                draft_id,
            )
            if got is False:
                logger.info(
                    "[social_drafts] draft %s already being posted (lock "
                    "contended) — skipping to avoid a duplicate promo",
                    draft_id[:8],
                )
                return {
                    "success": False,
                    "contended": True,
                    "error": "draft is already being posted by another approver",
                }
            try:
                return await self._approve_draft_locked(
                    draft_id, pool, site_config
                )
            finally:
                if got is not False:
                    # Best-effort release; a dropped connection releases the
                    # session lock anyway, so a failure here can't wedge it.
                    try:
                        await lock_conn.fetchval(
                            "SELECT pg_advisory_unlock($1, hashtext($2))",
                            _SOCIAL_POST_LOCK_NS,
                            draft_id,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "[social_drafts] advisory-unlock for draft %s "
                            "failed (lock frees on disconnect)",
                            draft_id[:8],
                            exc_info=True,
                        )

    async def _approve_draft_locked(
        self,
        draft_id: str,
        pool: Any,
        site_config: SiteConfig,
    ) -> dict[str, Any]:
        """The approve/fire critical section — see ``approve_draft``.

        Runs under the per-draft advisory lock that ``approve_draft`` holds, so
        exactly one caller executes this at a time for a given draft.
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, pipeline_task_id, post_id, platform, content, "
                "platform_config, status "
                "FROM social_post_drafts WHERE id = $1",
                draft_id,
            )
        if row is None:
            return {"success": False, "error": f"draft {draft_id} not found"}
        if row["status"] not in _APPROVABLE_STATUSES:
            return {
                "success": False,
                "error": f"draft status={row['status']} cannot be approved",
            }

        post = await _resolve_post(row, pool)
        if post is None:
            return {
                "success": False,
                "error": (
                    f"no posts row for task {row['pipeline_task_id']} — "
                    "publish the blog post first, then approve this draft "
                    "(social posts go out immediately, so the promoted link "
                    "must be live)"
                ),
            }
        if post["status"] != "published":
            return {
                "success": False,
                "error": (
                    f"post {str(post['id'])[:8]} is status={post['status']!r}, "
                    "not 'published' — the promoted link would 404. Wait for "
                    "the scheduled publish (or publish now), then approve"
                ),
            }

        platform = row["platform"]

        # Verify/repair the promoted URL against the live posts row, then
        # persist the repair + the post_id link on the draft before pushing.
        #
        # This is the last place the outbound link is touched, so it is where
        # the attribution tag is settled: whatever surface the copy was drafted
        # for, the URL that actually ships carries THIS draft's platform. That
        # is what corrects the short-form copy Bluesky and Mastodon inherit
        # from the tweet, and what back-fills a tag onto drafts generated
        # before tagging existed.
        content = row["content"]
        canonical_url = ""
        site_url = (site_config.get("site_url", "") or "").rstrip("/")
        if site_url:
            canonical_url = tag_for(
                site_config, f"{site_url}/posts/{post['slug']}", surface=platform
            )
            content = _ensure_post_url(content, canonical_url, site_url)
        if content != row["content"] or row["post_id"] is None:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE social_post_drafts
                    SET content = $2, post_id = $3
                    WHERE id = $1
                    """,
                    draft_id,
                    content,
                    post["id"],
                )
            if content != row["content"]:
                # Log the URL that actually shipped, tag and all — the whole
                # point of the tag is that it is auditable after the fact.
                logger.info(
                    "[social_drafts] draft %s: repaired promoted URL → %s",
                    draft_id[:8], canonical_url,
                )

        platform_config = _parse_jsonb(row["platform_config"])
        integration_key = _INTEGRATION_KEY.get(platform)
        integration_id = site_config.get(integration_key or "", "") if integration_key else ""
        if not integration_id:
            err = (
                f"Postiz integration UUID not configured for platform={platform!r} "
                f"(set app_settings.{integration_key})"
            )
            await _mark_failed(draft_id, err, pool)
            return {"success": False, "error": err}

        base_url = site_config.get("postiz_api_url", "http://postiz:3000")
        api_key = await site_config.get_secret("postiz_api_key", "")
        client = PostizClient(base_url=base_url, api_key=api_key)

        # Operator-tunable per-platform settings, merged over the draft's
        # platform_config. X carries a "made with AI" disclosure flag
        # (social_x_made_with_ai, default true — Glad Labs content is
        # AI-authored). setdefault lets a per-draft override still win.
        platform_settings = dict(platform_config)
        if platform == "twitter":
            made_with_ai = site_config.get("social_x_made_with_ai", "true")
            platform_settings.setdefault(
                "made_with_ai", str(made_with_ai).strip().lower() == "true"
            )

        result = await client.create_post(
            integration_id=integration_id,
            content=content,
            platform_type=_PLATFORM_TYPE.get(platform) or platform,
            platform_settings=platform_settings,
            upload_ids=[],
        )

        if result["success"]:
            await _mark_posted(draft_id, result.get("post_id"), pool)
            SOCIAL_DRAFT_POSTED_TOTAL.labels(platform=platform).inc()
            return {"success": True, "postiz_post_id": result.get("post_id")}

        await _mark_failed(draft_id, result.get("error", "unknown error"), pool)
        SOCIAL_DRAFT_FAILED_TOTAL.labels(platform=platform).inc()
        await notify_operator(
            f"[social] draft {draft_id[:8]} ({platform}) failed: {result.get('error')}",
            critical=True,
            site_config=site_config,
        )
        return {"success": False, "error": result.get("error")}

    async def schedule_draft(
        self,
        draft_id: str,
        when: str | datetime,
        pool: Any,
        site_config: SiteConfig,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Queue a draft to post at *when* instead of immediately.

        *when* accepts anything ``scheduling_service.parse_when`` does —
        ``"tomorrow 9am"``, ``"next monday 14:00"``, ISO 8601 — read in the
        operator's timezone, because an operator typing "9am" means 9am where
        they are. The stored value is UTC, per store-UTC/present-local.

        Does NOT check that the promoted post is live: scheduling ahead of
        publication is the normal case. ``fire_due_drafts`` re-checks the
        publish gate when the slot arrives.

        Refuses a slot in the past unless ``force`` (which fires on the next
        sweep) — a past slot is nearly always a typo'd year or a forgotten
        am/pm, and silently posting immediately is the wrong recovery.
        """
        from services.scheduling_service import parse_when

        tz = site_config.timezone
        try:
            target = parse_when(when, tz=tz)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        now = datetime.now(timezone.utc)
        if target <= now and not force:
            return {
                "success": False,
                "error": (
                    f"{target.astimezone(tz).isoformat()} is in the past "
                    f"(now {now.astimezone(tz).isoformat()}) — pass force to "
                    f"queue it for the next sweep anyway, or give a future time"
                ),
            }

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM social_post_drafts WHERE id = $1", draft_id
            )
            if row is None:
                return {"success": False, "error": f"draft {draft_id} not found"}
            if row["status"] not in _APPROVABLE_STATUSES:
                return {
                    "success": False,
                    "error": (
                        f"draft status={row['status']} cannot be scheduled "
                        f"(only {'/'.join(_APPROVABLE_STATUSES)})"
                    ),
                }
            await conn.execute(
                """
                UPDATE social_post_drafts
                SET status = 'scheduled', scheduled_at = $2
                WHERE id = $1
                """,
                draft_id,
                target,
            )
        logger.info(
            "[social_drafts] draft %s scheduled for %s",
            draft_id[:8], target.astimezone(tz).isoformat(),
        )
        return {
            "success": True,
            "scheduled_at": target.isoformat(),
            "previous_status": row["status"],
        }

    async def unschedule_draft(self, draft_id: str, pool: Any) -> dict[str, Any]:
        """Pull a draft back out of the queue, returning it to ``pending``.

        The draft goes back to needing an operator decision rather than being
        rejected — "not at that time" is not "not at all".
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE social_post_drafts
                SET status = 'pending', scheduled_at = NULL
                WHERE id = $1 AND status = 'scheduled'
                RETURNING id::text
                """,
                draft_id,
            )
        if row is None:
            return {
                "success": False,
                "error": f"draft {draft_id} is not scheduled",
            }
        return {"success": True}

    async def fire_due_drafts(
        self,
        pool: Any,
        site_config: SiteConfig,
        *,
        limit: int | None = None,
        max_lateness_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Post every scheduled draft whose slot has arrived.

        Each due draft goes through ``approve_draft``, so the publish gate
        and URL repair run at fire time — the point of holding the queue here
        rather than in Postiz.

        A draft more than ``max_lateness_minutes`` past its slot is NOT
        fired. It stays ``scheduled`` and raises a finding for the operator
        to reschedule or approve by hand. The alternative — firing whenever
        the worker happens to come back — means an outage silently turns a
        timed promo into an untimed one, hours off the slot that was chosen
        for a reason. Fail loud beats quietly-wrong.
        """
        batch = (
            limit
            if limit is not None
            else int(site_config.get("social_schedule_fire_batch_size", "10"))
        )
        lateness = (
            max_lateness_minutes
            if max_lateness_minutes is not None
            else int(site_config.get("social_schedule_max_lateness_minutes", "180"))
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id::text AS id, platform, scheduled_at
                FROM social_post_drafts
                WHERE status = 'scheduled'
                  AND scheduled_at IS NOT NULL
                  AND scheduled_at <= now()
                ORDER BY scheduled_at ASC
                LIMIT $1
                """,
                batch,
            )

        now = datetime.now(timezone.utc)
        posted = 0
        failed = 0
        blocked = 0
        overdue = 0
        for row in rows:
            draft_id = row["id"]
            late_minutes = (now - row["scheduled_at"]).total_seconds() / 60.0
            if late_minutes > lateness:
                overdue += 1
                _emit_overdue_finding(
                    draft_id, row["platform"], row["scheduled_at"], late_minutes
                )
                continue
            try:
                result = await self.approve_draft(draft_id, pool, site_config)
            except Exception as exc:
                failed += 1
                logger.error(
                    "[social_drafts] firing draft %s raised: %s", draft_id[:8], exc
                )
                continue
            if result.get("success"):
                posted += 1
                logger.info(
                    "[social_drafts] fired scheduled draft %s (%s)",
                    draft_id[:8], row["platform"],
                )
            elif result.get("contended"):
                # Another approver holds the per-draft lock and is posting it
                # right now — not our failure and not blocked; the holder owns
                # the outcome. Don't count it, so metrics stay honest.
                logger.info(
                    "[social_drafts] scheduled draft %s already being posted "
                    "by another approver — skipping",
                    draft_id[:8],
                )
            elif await self._still_scheduled(draft_id, pool):
                # approve_draft's publish gate refused (post not live yet) and
                # left the status alone. The draft keeps its slot and is
                # retried on the next sweep, until it goes overdue.
                blocked += 1
                logger.info(
                    "[social_drafts] scheduled draft %s not fired yet: %s",
                    draft_id[:8], result.get("error"),
                )
            else:
                failed += 1

        return {
            "due": len(rows),
            "posted": posted,
            "blocked": blocked,
            "failed": failed,
            "overdue": overdue,
        }

    async def _still_scheduled(self, draft_id: str, pool: Any) -> bool:
        """True when the draft kept its ``scheduled`` status (gate-blocked).

        Distinguishes "the publish gate said not yet" (status untouched, try
        again next sweep) from "Postiz rejected it" (``_mark_failed`` moved
        it to ``failed``, which is the retry job's problem now).
        """
        async with pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM social_post_drafts WHERE id = $1", draft_id
            )
        return status == "scheduled"

    async def auto_schedule_ready_drafts(
        self, pool: Any, site_config: SiteConfig
    ) -> dict[str, Any]:
        """Assign drip slots to pending drafts whose post has gone live.

        The auto half of scheduling. Two ways to express when a promo fires,
        and a platform opts in through either:

        * ``social_schedule_prime_times`` — the hours that channel is worth
          posting to (``twitter=09:00,12:30,17:00``). The slot is the next
          listed time at or after the floor. **This is what a post published
          at 11pm needs**: the promo lands at 09:00, not at midnight.
        * ``social_schedule_offsets`` — a delay from publish
          (``linkedin=3h``). On its own it IS the slot; alongside prime
          times it is the floor the scan starts from, so "at least 3h after
          publish, then the next good hour".

        Prime times take precedence over ``social_schedule_quiet_hours`` for
        platforms that declare them. A quiet window only says where NOT to
        post, so it clamps every displaced promo onto the window's edge —
        publishing at 11pm with a 22:00-07:00 window put four platforms on
        the same 07:00 minute, destroying the stagger. Naming the good hours
        is strictly better than naming the bad ones.

        Slots are de-duplicated per platform against drafts already queued,
        so several posts published overnight spread across that platform's
        listed hours instead of firing as one burst.

        Two deliberate gates, both defaulting closed:
        ``social_schedule_enabled`` must be on, AND the platform must appear
        in one of the two maps. A platform in neither is never auto-slotted,
        so turning the switch on alone changes nothing. Enabling this means
        promo copy ships on the strength of the POST's approval rather than
        its own per-draft review.

        Backfill behaviour: for a post published longer ago than its offsets,
        slots would all be in the past and fire in one burst. The anchor
        moves to ``now`` in that case, so the platform stagger is preserved
        instead of collapsing.
        """
        if not _is_true(site_config.get("social_schedule_enabled", "false")):
            return {"scheduled": 0, "detail": "social_schedule_enabled=false"}

        offsets = parse_offsets(site_config.get("social_schedule_offsets", ""))
        prime_times = parse_prime_times(
            site_config.get("social_schedule_prime_times", "")
        )
        # A platform opts in through EITHER setting. Prime times alone is the
        # common case ("X posts at 9am"); an offset alone keeps the original
        # relative-drip behaviour; both together means the offset is a floor
        # the prime-time scan starts from.
        eligible = sorted(set(offsets) | set(prime_times))
        if not eligible:
            return {
                "scheduled": 0,
                "detail": "social_schedule_offsets/prime_times empty",
            }

        from services.scheduling_service import (
            next_allowed_time,
            parse_quiet_hours,
        )

        tz = site_config.timezone
        quiet_spec = site_config.get("social_schedule_quiet_hours", "")
        try:
            quiet = parse_quiet_hours(quiet_spec) if quiet_spec else None
        except ValueError as exc:
            # A malformed window must not silently mean "no quiet hours" —
            # that posts inside exactly the window the operator carved out.
            logger.error(
                "[social_drafts] social_schedule_quiet_hours=%r invalid (%s) "
                "— auto-scheduling paused until it parses",
                quiet_spec, exc,
            )
            return {"scheduled": 0, "detail": f"invalid quiet hours: {exc}"}

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.id::text AS id, d.platform, p.published_at
                FROM social_post_drafts d
                JOIN posts p
                  ON (d.post_id IS NOT NULL AND p.id = d.post_id)
                  OR (d.post_id IS NULL
                      AND p.metadata->>'pipeline_task_id' = d.pipeline_task_id)
                WHERE d.status = 'pending'
                  AND d.scheduled_at IS NULL
                  AND p.status = 'published'
                  AND p.published_at IS NOT NULL
                  AND d.platform = ANY($1::text[])
                ORDER BY p.published_at ASC, d.platform ASC
                """,
                eligible,
            )
            # Slots already claimed on each platform, so a second post doesn't
            # land on top of the first one's 09:00. Ordered scheduling above
            # (oldest post first) keeps the assignment stable across sweeps.
            claimed = await conn.fetch(
                """
                SELECT platform, scheduled_at
                FROM social_post_drafts
                WHERE status = 'scheduled' AND scheduled_at IS NOT NULL
                  AND scheduled_at > now()
                """
            )

        taken: dict[str, set[datetime]] = {}
        for row in claimed:
            taken.setdefault(row["platform"], set()).add(
                row["scheduled_at"].astimezone(tz)
            )

        now = datetime.now(timezone.utc)
        scheduled = 0
        for row in rows:
            platform = row["platform"]
            offset = offsets.get(platform, timedelta(0))
            published_at = row["published_at"]
            # Anchor forward for posts published long enough ago that their
            # whole drip would already be due.
            anchor = published_at if published_at + offset > now else now
            earliest = (anchor + offset).astimezone(tz)

            times = prime_times.get(platform)
            slot = None
            if times:
                # Prime times are a positive statement of when this channel is
                # worth posting to, so they win over the quiet window — which
                # only says where NOT to post and would otherwise pile every
                # displaced promo onto the window's edge.
                slot = next_prime_slot(earliest, times, taken.get(platform, set()))
            if slot is None:
                slot = next_allowed_time(earliest, quiet)

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE social_post_drafts
                    SET status = 'scheduled', scheduled_at = $2
                    WHERE id = $1 AND status = 'pending'
                    """,
                    row["id"],
                    slot.astimezone(timezone.utc),
                )
            taken.setdefault(platform, set()).add(slot)
            scheduled += 1
            logger.info(
                "[social_drafts] auto-scheduled draft %s (%s) for %s%s",
                row["id"][:8], platform, slot.isoformat(),
                " (prime time)" if times else "",
            )

        return {
            "scheduled": scheduled,
            "detail": f"auto-scheduled {scheduled} draft(s)",
        }

    async def reject_draft(self, draft_id: str, pool: Any) -> None:
        """Reject a draft (terminal). Also clears any queued slot.

        Rejecting a *scheduled* draft has to drop ``scheduled_at`` as well —
        a rejected row that keeps a fire time reads as still-queued to the
        console and to anything summarising the upcoming queue.
        """
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE social_post_drafts "
                "SET status = 'rejected', scheduled_at = NULL WHERE id = $1",
                draft_id,
            )

    async def edit_draft(
        self,
        draft_id: str,
        content: str,
        platform_config: dict[str, Any] | None,
        pool: Any,
    ) -> None:
        updates: list[str] = ["content = $2"]
        args: list[Any] = [draft_id, content]
        if platform_config is not None:
            updates.append(f"platform_config = ${len(args) + 1}")
            args.append(json.dumps(platform_config))
        sql = f"UPDATE social_post_drafts SET {', '.join(updates)} WHERE id = $1"  # nosec B608 - updates entries are hardcoded column literals with computed placeholder indices; values are bind params
        async with pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def list_drafts(
        self,
        post_id: str | None,
        pipeline_task_id: str | None,
        status: str | None,
        pool: Any,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by_schedule: bool = False,
    ) -> SocialDraftPage:
        """List drafts, each carrying its resolved post_status, title, and id.

        post_status lets callers (the console's action inbox) distinguish a
        genuinely-approvable draft from one that would 409 on approve_draft's
        post-link gate (post not 'published' yet, or no posts row at all) —
        the same resolution _resolve_post uses: post_id when linked, else the
        latest posts row matching pipeline_task_id metadata.

        title/resolved_post_id let callers identify which article a draft
        promotes even before that article publishes: posts.title/posts.id
        win once a posts row exists (can resolve before social_post_drafts.
        post_id itself gets backfilled at publish time), else pipeline_tasks.
        topic is the fallback label for a task with no posts row yet.

        **Live rows sort first, ahead of created_at DESC.** The table only
        grows (one row per platform per post, and terminal rows are never
        pruned — 67 of 77 rows were already ``posted``/``rejected`` tombstones
        when this cap landed), so a plain recency cap would eventually push an
        old *pending* draft out of the window — silently stranding an approval
        that the console's action inbox derives from exactly this list. Live
        here means ``_LIVE_STATUSES`` (pending/scheduled/failed): the statuses
        ``approve_draft`` still accepts, so they are the rows that can still
        need — or are awaiting — action. Sorting them to the front means the
        cap can only ever hide tombstones.

        ``limit=None`` emits no LIMIT clause. The HTTP surface always passes
        one (``routes/social_routes.py``); the unbounded form is for callers
        that genuinely need the full set.

        ``order_by_schedule`` swaps the within-group tiebreak from recency to
        fire time — what the upcoming-queue view (``poindexter social
        queue``) wants, and only that view.
        """
        scope: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            scope.append(f"d.post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            scope.append(f"d.pipeline_task_id = ${len(args)}")
        # status_counts deliberately spans every status within the post/task
        # scope, so it stays a truthful breakdown even when the caller filters
        # to one status. `total` is the narrower "rows matching ALL filters".
        scope_where = f"WHERE {' AND '.join(scope)}" if scope else ""
        counts_sql = f"""
            SELECT d.status, count(*) AS n
            FROM social_post_drafts d
            {scope_where}
            GROUP BY d.status
        """  # nosec B608 - scope entries are hardcoded column literals with computed placeholder indices; values are bind params

        conditions = list(scope)
        if status:
            args.append(status)
            conditions.append(f"d.status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        page_args = list(args)
        page_args.append(list(_LIVE_STATUSES))
        live_sort = f"(d.status = ANY(${len(page_args)}::text[])) DESC"
        # Recency is the right default for a review list, but wrong for the
        # upcoming-queue view, which wants fire order. Kept an explicit flag
        # rather than always tie-breaking on scheduled_at: a `posted` row
        # retains the scheduled_at it fired from, so a blanket sort on it
        # would scramble the tombstone half out of recency order.
        secondary_sort = (
            "d.scheduled_at ASC NULLS LAST, d.created_at DESC"
            if order_by_schedule
            else "d.created_at DESC"
        )
        pagination = ""
        if limit is not None:
            page_args.append(limit)
            pagination = f"LIMIT ${len(page_args)}"
        if offset:
            page_args.append(offset)
            pagination = f"{pagination} OFFSET ${len(page_args)}".strip()
        sql = f"""
            SELECT d.*,
                   rp.status AS resolved_post_status,
                   COALESCE(rp.title, pt.topic) AS article_title,
                   COALESCE(d.post_id, rp.id) AS resolved_post_id
            FROM social_post_drafts d
            LEFT JOIN pipeline_tasks pt ON pt.task_id = d.pipeline_task_id
            LEFT JOIN LATERAL (
                SELECT p.id, p.title, p.status
                FROM posts p
                WHERE (d.post_id IS NOT NULL AND p.id = d.post_id)
                   OR (d.post_id IS NULL AND p.metadata->>'pipeline_task_id' = d.pipeline_task_id)
                ORDER BY p.created_at DESC
                LIMIT 1
            ) rp ON true
            {where}
            ORDER BY {live_sort}, {secondary_sort}
            {pagination}
        """  # nosec B608 - conditions/live_sort/secondary_sort entries are hardcoded column literals with computed placeholder indices; values are bind params
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *page_args)
            count_rows = await conn.fetch(counts_sql, *args[: len(scope)])
        status_counts = {r["status"]: int(r["n"]) for r in count_rows}
        total = (
            status_counts.get(status, 0)
            if status
            else sum(status_counts.values())
        )
        return SocialDraftPage(
            rows=[_row_to_dataclass(r) for r in rows],
            total=total,
            status_counts=status_counts,
        )

    async def retry_draft(
        self, draft_id: str, pool: Any, site_config: SiteConfig
    ) -> dict[str, Any]:
        """Increment retry_count, reset to pending, and re-fire approve_draft."""
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE social_post_drafts
                SET retry_count = retry_count + 1,
                    last_retry_at = now(),
                    status = 'pending'
                WHERE id = $1 AND status = 'failed'
                """,
                draft_id,
            )
        return await self.approve_draft(draft_id, pool, site_config)

    async def backfill_post_id(
        self, pipeline_task_id: str, post_id: str, pool: Any
    ) -> None:
        """Set post_id on all pending/failed/posted drafts for this task."""
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE social_post_drafts
                SET post_id = $2
                WHERE pipeline_task_id = $1 AND post_id IS NULL
                """,
                pipeline_task_id,
                post_id,
            )

    async def find_posts_missing_social_coverage(
        self, pool: Any, lookback_days: int
    ) -> list[dict[str, Any]]:
        """Published posts whose task's template includes social.generate_drafts.

        Filtering on the template's ``graph_def`` (not a literal draft-count
        comparison) is deliberate: the atom's own ``existing_draft_keys``
        idempotency guard is what actually decides which platforms still
        need generating — this query only narrows down which posts are
        worth that cheap re-check, not which platforms are missing.

        Returns dicts carrying exactly the fields
        ``modules.content.atoms.social_generate_drafts.run`` needs to
        regenerate: pipeline_task_id, title, slug, content, excerpt,
        seo_description, seo_keywords.
        """
        sql = """
            SELECT p.metadata->>'pipeline_task_id' AS pipeline_task_id,
                   p.title, p.slug, p.content, p.excerpt,
                   p.seo_description, p.seo_keywords
            FROM posts p
            JOIN pipeline_tasks pt
                ON pt.task_id = p.metadata->>'pipeline_task_id'
            WHERE p.status = 'published'
              AND p.published_at > now() - make_interval(days => $1::integer)
              AND p.metadata->>'pipeline_task_id' IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM pipeline_templates tpl,
                       jsonb_array_elements(tpl.graph_def->'nodes') AS node
                  WHERE tpl.slug = pt.template_slug
                    AND tpl.active = true
                    AND node->>'atom' = 'social.generate_drafts'
              )
            ORDER BY p.published_at DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, lookback_days)
        return [dict(r) for r in rows]

    async def reconcile_missing_drafts(
        self, pool: Any, site_config: SiteConfig, lookback_days: int
    ) -> dict[str, Any]:
        """Find + regenerate drafts for posts with incomplete platform coverage.

        Root-cause fix for poindexter#863: social.generate_drafts can fail
        mid-generation without a trace (the atom catches and logs but does
        not persist anything), so RetryFailedSocialDraftsJob has nothing to
        retry — no row was ever created. This re-invokes the atom's own
        ``run()`` for each candidate; its ``existing_draft_keys`` idempotency
        guard makes a post with full coverage a cheap no-op (one query, zero
        LLM calls), so calling it again is always safe.

        The atom is reached through ``modules.content.api`` (the thin-adapter
        boundary substrate uses to call into the content module) via a lazy
        import — the atom imports ``SocialDraftsService`` at module level, so
        a top-level import here would be circular.
        """
        candidates = await self.find_posts_missing_social_coverage(
            pool, lookback_days
        )
        from modules.content.api import generate_social_drafts

        checked = 0
        created = 0
        errors: list[str] = []
        for post in candidates:
            task_id = post.get("pipeline_task_id") or ""
            if not task_id:
                continue
            checked += 1
            state = {
                "site_config": site_config,
                "task_id": task_id,
                "title": post.get("title") or "",
                "post_slug": post.get("slug") or "",
                "content": post.get("content") or "",
                "excerpt": post.get("excerpt") or post.get("seo_description") or "",
                "seo_keywords": post.get("seo_keywords") or "",
                "database_service": _PoolAdapter(pool),
            }
            try:
                before = await self.existing_draft_keys(task_id, pool)
                await generate_social_drafts(state)
                after = await self.existing_draft_keys(task_id, pool)
                created += len(after - before)
            except Exception as exc:
                logger.error(
                    "[social_drafts] reconcile task %s raised: %s",
                    task_id[:8], exc,
                )
                errors.append(f"{task_id[:8]}: {exc}")
        return {
            "candidates_checked": checked,
            "drafts_created": created,
            "errors": errors,
        }

    async def cancel_orphaned_for_rejected_tasks(self, pool: Any) -> int:
        """Cancel pending/failed drafts whose content task was terminally rejected.

        Path-independent reaper. Social drafts are generated speculatively at
        pipeline finalize — before operator sign-off — so a post rejected
        afterward via ANY path (operator gate reject, max-retry fail, QA
        terminal) strands its promos in ``pending``. Keying off the task's
        terminal-reject status (see ``_TERMINAL_REJECT_TASK_STATUSES``) rather
        than hooking one reject entry point catches every path AND back-fills
        historical orphans on first run. Already-``posted`` drafts are left
        untouched — retracting a live social post is a separate concern.

        ``scheduled`` drafts are reaped alongside ``pending``/``failed`` ones,
        and matter MORE than either: a pending orphan just sits in the inbox,
        but a scheduled orphan has a timer on it and would fire a promo for a
        post that was rejected. ``scheduled_at`` is cleared so the row can't
        be read as still-queued.

        Returns the number of drafts cancelled (``status`` → ``rejected``).
        """
        async with pool.acquire() as conn:
            command_tag = await conn.execute(
                """
                UPDATE social_post_drafts d
                SET status = 'rejected', scheduled_at = NULL
                FROM pipeline_tasks t
                WHERE d.pipeline_task_id = t.task_id
                  AND d.status = ANY($2::text[])
                  AND t.status = ANY($1::text[])
                """,
                list(_TERMINAL_REJECT_TASK_STATUSES),
                list(_LIVE_STATUSES),
            )
        return _pg_command_rowcount(command_tag)


class _PoolAdapter:
    """Minimal ``database_service``-shaped wrapper around a bare pool.

    ``social_generate_drafts.run`` reaches the pool via
    ``state['database_service'].pool`` (the graph_def seam), never a bare
    ``state['pool']`` — see poindexter#855. reconcile_missing_drafts already
    has a bare pool, not a real DatabaseService, so this adapts it.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def parse_offsets(spec: str) -> dict[str, timedelta]:
    """Parse ``'twitter=0m,linkedin=3h'`` into ``{platform: timedelta}``.

    Durations use the same ``30m`` / ``2h`` / ``1d`` grammar as the rest of
    scheduling (``scheduling_service.parse_duration``). Unknown platform
    names and unparseable durations are logged and dropped rather than
    raising: one typo in a five-platform map should cost that platform its
    drip, not stop the other four from ever being scheduled.

    Empty/blank spec returns ``{}`` — the documented "auto-slot nothing".
    """
    from services.scheduling_service import parse_duration

    out: dict[str, timedelta] = {}
    if not spec or not spec.strip():
        return out
    for chunk in spec.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        platform, sep, raw_duration = piece.partition("=")
        platform = platform.strip().lower()
        if not sep or not platform:
            logger.warning(
                "[social_drafts] social_schedule_offsets: skipping %r "
                "(expected platform=duration, e.g. linkedin=3h)", piece,
            )
            continue
        if platform not in _PLATFORM_TYPE:
            logger.warning(
                "[social_drafts] social_schedule_offsets: unknown platform "
                "%r — known: %s", platform, ", ".join(sorted(_PLATFORM_TYPE)),
            )
            continue
        try:
            out[platform] = parse_duration(raw_duration.strip())
        except ValueError as exc:
            logger.warning(
                "[social_drafts] social_schedule_offsets: bad duration for "
                "%s (%s) — skipping", platform, exc,
            )
    return out


def parse_prime_times(spec: str) -> dict[str, list[time]]:
    """Parse ``'twitter=09:00,12:30; linkedin=08:00'`` → ``{platform: [time]}``.

    Each platform's times are the hours that channel is worth posting in,
    operator-local. Times are sorted and de-duplicated so the "next slot at
    or after X" scan can walk them in order.

    Same lenient-per-entry contract as :func:`parse_offsets`: an unknown
    platform or an unparseable clock time is logged and dropped rather than
    raising, so one typo costs that platform its prime times instead of
    stopping the other four from being scheduled at all.
    """
    out: dict[str, list[time]] = {}
    if not spec or not spec.strip():
        return out
    for chunk in spec.split(";"):
        piece = chunk.strip()
        if not piece:
            continue
        platform, sep, raw_times = piece.partition("=")
        platform = platform.strip().lower()
        if not sep or not platform:
            logger.warning(
                "[social_drafts] social_schedule_prime_times: skipping %r "
                "(expected platform=HH:MM,HH:MM)", piece,
            )
            continue
        if platform not in _PLATFORM_TYPE:
            logger.warning(
                "[social_drafts] social_schedule_prime_times: unknown platform "
                "%r — known: %s", platform, ", ".join(sorted(_PLATFORM_TYPE)),
            )
            continue
        times: list[time] = []
        for raw in raw_times.split(","):
            token = raw.strip()
            if not token:
                continue
            parsed = _parse_clock(token)
            if parsed is None:
                logger.warning(
                    "[social_drafts] social_schedule_prime_times: bad time %r "
                    "for %s — skipping", token, platform,
                )
                continue
            times.append(parsed)
        if times:
            out[platform] = sorted(set(times))
        else:
            logger.warning(
                "[social_drafts] social_schedule_prime_times: %s had no usable "
                "times — it will fall back to its offset", platform,
            )
    return out


def _parse_clock(token: str) -> time | None:
    """``'09:00'`` / ``'9:00'`` / ``'17:30'`` → ``time``. None if unparseable."""
    parts = token.split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return None
    return time(hh, mm)


# How far forward the prime-time scan will look before giving up. Only
# reachable if a platform's time list is somehow empty (parse_prime_times
# drops those) — a guard against an unbounded loop, not an expected path.
_PRIME_TIME_SCAN_DAYS = 14


def next_prime_slot(
    earliest: datetime,
    times: list[time],
    taken: set[datetime],
) -> datetime | None:
    """First prime time at or after *earliest* that isn't already *taken*.

    *earliest* is the floor — publish time plus the platform's offset — so a
    post that goes live at 10am takes that day's 12:30 slot rather than
    waiting for tomorrow's 09:00. A post published at 11pm rolls to the next
    morning's first slot, which is the whole point: the promo lands in prime
    time instead of at whatever hour the post happened to finish.

    *taken* holds slots already claimed by other drafts on the same platform.
    Without it, three posts published overnight would all grab 09:00 and fire
    as a burst — the exact "same link everywhere at once" pattern the stagger
    exists to avoid. Each collision advances to the platform's next slot.

    Returns None if nothing is free within ``_PRIME_TIME_SCAN_DAYS``; the
    caller falls back to the plain offset slot rather than dropping the draft.
    """
    if not times:
        return None
    day = earliest.date()
    for _ in range(_PRIME_TIME_SCAN_DAYS):
        for clock in times:
            candidate = datetime.combine(day, clock, tzinfo=earliest.tzinfo)
            if candidate >= earliest and candidate not in taken:
                return candidate
        day = day + timedelta(days=1)
    return None


def _emit_overdue_finding(
    draft_id: str, platform: str, slot: datetime, late_minutes: float
) -> None:
    """Surface a draft that missed its window by more than the grace period.

    Deduped on the draft id, so a permanently-overdue row raises once rather
    than every sweep.
    """
    try:
        from utils.findings import emit_finding

        emit_finding(
            source="services.social_drafts",
            kind="social_draft_overdue",
            title=f"Scheduled {platform} promo missed its slot",
            body=(
                f"Draft {draft_id[:8]} was due {slot.isoformat()} "
                f"({int(late_minutes)} min ago), past the "
                f"social_schedule_max_lateness_minutes grace period. It was "
                f"NOT posted. Reschedule it (`poindexter social schedule "
                f"{draft_id} <when>`), post it now (`poindexter social "
                f"approve {draft_id}`), or drop it (`poindexter social "
                f"reject {draft_id}`)."
            ),
            severity="warn",
            dedup_key=f"social-draft-overdue:{draft_id}",
        )
    except Exception as exc:  # noqa: BLE001
        # Reporting the miss must never take down the sweep — the remaining
        # due drafts still deserve their shot. Warn (visible in Loki) rather
        # than swallow (scripts/ci/lint_silent_excepts.py).
        logger.warning("[social_drafts] overdue finding emit failed: %s", exc)


async def _resolve_post(draft_row: Any, pool: Any) -> Any:
    """Resolve the posts row a draft promotes.

    By ``post_id`` when already linked, else through the canonical
    ``posts.metadata->>'pipeline_task_id'`` seam (stamped at insert by
    ``publish_post_from_task``). Returns ``None`` when the post has not
    been created yet (task still awaiting approval / staged-only).
    """
    async with pool.acquire() as conn:
        if draft_row["post_id"]:
            return await conn.fetchrow(
                "SELECT id, slug, status FROM posts WHERE id = $1",
                draft_row["post_id"],
            )
        return await conn.fetchrow(
            """
            SELECT id, slug, status FROM posts
            WHERE metadata->>'pipeline_task_id' = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            draft_row["pipeline_task_id"],
        )


def _ensure_post_url(content: str, canonical_url: str, site_url: str) -> str:
    """Guarantee *content* promotes *canonical_url*.

    Rewrites any existing ``{site_url}/posts/…`` URL — apex/www tolerant, so
    it also catches the dead empty-slug ``…/posts/`` the pre-fix atom baked
    in and any stale predicted slug after a title edit. Appends the URL when
    the copy carries none: a promo post without a link is useless.
    """
    host = urlparse(site_url).netloc.removeprefix("www.")
    if host:
        pattern = re.compile(
            rf"https?://(?:www\.)?{re.escape(host)}/posts/[^\s\"'<>()\[\]]*"
        )
        if pattern.search(content):
            # Literal replacement: canonical_url is data, not a template, so it
            # must not be scanned for backreferences (\g, \1). Harmless for a
            # bare slug URL, but the tag appended by distribution_ref makes this
            # a URL with operator-configurable content.
            return pattern.sub(lambda _m: canonical_url, content)
    if canonical_url in content:
        return content
    return f"{content.rstrip()} {canonical_url}".strip()


async def _mark_posted(draft_id: str, postiz_post_id: str | None, pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE social_post_drafts
            SET status = 'posted', postiz_post_id = $2, posted_at = now(),
                approved_at = COALESCE(approved_at, now())
            WHERE id = $1
            """,
            draft_id,
            postiz_post_id,
        )


async def _mark_failed(draft_id: str, error: str, pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE social_post_drafts
            SET status = 'failed', error = $2
            WHERE id = $1
            """,
            draft_id,
            error,
        )


def _pg_command_rowcount(command_tag: Any) -> int:
    """Affected-row count from an asyncpg command tag like ``'UPDATE 3'``.

    Returns 0 on any unexpected shape (empty tag, non-numeric suffix) rather
    than raising — the caller only needs it for logging/telemetry.
    """
    try:
        return int(str(command_tag).split()[-1])
    except (ValueError, IndexError):
        return 0


def _parse_jsonb(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            return dict(json.loads(value))
        except Exception:  # silent-ok: malformed stored JSONB returns empty dict rather than crashing callers
            return {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _row_to_dataclass(row: Any) -> SocialDraftRow:
    return SocialDraftRow(
        id=str(row["id"]),
        pipeline_task_id=str(row["pipeline_task_id"]),
        post_id=str(row["post_id"]) if row["post_id"] else None,
        platform=row["platform"],
        content=row["content"],
        platform_config=_parse_jsonb(row["platform_config"]),
        status=row["status"],
        postiz_post_id=row["postiz_post_id"],
        error=row["error"],
        retry_count=row["retry_count"],
        last_retry_at=row["last_retry_at"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        posted_at=row["posted_at"],
        post_status=row["resolved_post_status"],
        title=row["article_title"],
        resolved_post_id=(
            str(row["resolved_post_id"]) if row["resolved_post_id"] else None
        ),
        scheduled_at=_optional_column(row, "scheduled_at"),
    )


def _optional_column(row: Any, name: str) -> Any:
    """Read *name* off *row*, or None when the row doesn't carry it.

    ``list_drafts`` selects ``d.*`` so the column is always present there,
    but tests and other callers hand in trimmed row mappings. Tolerating the
    absence keeps a new column from breaking them.
    """
    try:
        return row[name]
    except (KeyError, IndexError):
        return None
