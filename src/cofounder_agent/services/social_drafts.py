"""SocialDraftsService — create, approve, reject, and query social post drafts.

All surfaces (CLI, API, MCP) delegate here. This module is the only place
that writes to social_post_drafts or calls PostizClient.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

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
_TERMINAL_REJECT_TASK_STATUSES: tuple[str, ...] = (
    "rejected_final",
    "rejected",
    "dismissed",
)


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
        (poindexter#833). The guarded insert skips when the key already has an
        active (``pending``/``failed``) or ``posted`` draft — returning the
        existing row's id — with the ``ux_social_post_drafts_active_key``
        partial unique index as the ON CONFLICT race backstop. A ``rejected``-
        only key inserts fresh: an operator reject followed by a regen loop
        legitimately produces new copy to review.

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
                      AND status IN ('pending', 'failed', 'posted')
                )
                ON CONFLICT (
                    pipeline_task_id, platform,
                    (COALESCE(platform_config->>'subreddit', ''))
                ) WHERE status IN ('pending', 'failed')
                DO NOTHING
                RETURNING id::text
                """,
                pipeline_task_id,
                platform,
                content,
                json.dumps(platform_config),
                subreddit_key,
            )
            if new_id is None:
                existing_id: str | None = await conn.fetchval(
                    """
                    SELECT id::text FROM social_post_drafts
                    WHERE pipeline_task_id = $1
                      AND platform = $2
                      AND COALESCE(platform_config->>'subreddit', '') = $3
                      AND status IN ('pending', 'failed', 'posted')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    pipeline_task_id,
                    platform,
                    subreddit_key,
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

        The generate atom consults this before spending LLM calls: keys with
        an active (``pending``/``failed``) or ``posted`` draft are skipped on
        re-runs. ``rejected`` drafts don't count — rejection is per-copy, and
        a regen loop after a reject should offer fresh copy.
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT platform,
                       COALESCE(platform_config->>'subreddit', '') AS subreddit
                FROM social_post_drafts
                WHERE pipeline_task_id = $1
                  AND status IN ('pending', 'failed', 'posted')
                """,
                pipeline_task_id,
            )
        return {(r["platform"], r["subreddit"]) for r in rows}

    async def approve_draft(
        self,
        draft_id: str,
        pool: Any,
        site_config: SiteConfig,
    ) -> dict[str, Any]:
        """Approve a pending or failed draft: call Postiz immediately.

        Post-link gate (social-drafts linking bug): approving pushes to the
        platform NOW, so the blog post being promoted must already be live
        (``posts.status='published'``) and the URL in the copy is verified /
        repaired against the live posts row first. A blocked approve leaves
        the draft ``pending`` — it is not a failure, so it neither consumes
        ``RetryFailedSocialDraftsJob`` retries nor needs a reset; approve
        again once the post is live.
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
        if row["status"] not in ("pending", "failed"):
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

        # Verify/repair the promoted URL against the live posts row, then
        # persist the repair + the post_id link on the draft before pushing.
        content = row["content"]
        site_url = (site_config.get("site_url", "") or "").rstrip("/")
        if site_url:
            canonical_url = f"{site_url}/posts/{post['slug']}"
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
                logger.info(
                    "[social_drafts] draft %s: repaired promoted URL → "
                    "%s/posts/%s",
                    draft_id[:8], site_url, post["slug"],
                )

        platform = row["platform"]
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

    async def reject_draft(self, draft_id: str, pool: Any) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE social_post_drafts SET status = 'rejected' WHERE id = $1",
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
        sql = f"UPDATE social_post_drafts SET {', '.join(updates)} WHERE id = $1"
        async with pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def list_drafts(
        self,
        post_id: str | None,
        pipeline_task_id: str | None,
        status: str | None,
        pool: Any,
    ) -> list[SocialDraftRow]:
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
        """
        conditions: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            conditions.append(f"d.post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            conditions.append(f"d.pipeline_task_id = ${len(args)}")
        if status:
            args.append(status)
            conditions.append(f"d.status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
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
            ORDER BY d.created_at DESC
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [_row_to_dataclass(r) for r in rows]

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

        Returns the number of drafts cancelled (``status`` → ``rejected``).
        """
        async with pool.acquire() as conn:
            command_tag = await conn.execute(
                """
                UPDATE social_post_drafts d
                SET status = 'rejected'
                FROM pipeline_tasks t
                WHERE d.pipeline_task_id = t.task_id
                  AND d.status IN ('pending', 'failed')
                  AND t.status = ANY($1::text[])
                """,
                list(_TERMINAL_REJECT_TASK_STATUSES),
            )
        return _pg_command_rowcount(command_tag)


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
            return pattern.sub(canonical_url, content)
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
    )
