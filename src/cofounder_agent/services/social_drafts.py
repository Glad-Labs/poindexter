"""SocialDraftsService — create, approve, reject, and query social post drafts.

All surfaces (CLI, API, MCP) delegate here. This module is the only place
that writes to social_post_drafts or calls PostizClient.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
    "reddit": "reddit",
    "tiktok": "tiktok",
    "instagram_reels": "instagram",
}

# platform → postiz_integration_id_* setting key
_INTEGRATION_KEY: dict[str, str] = {
    "twitter": "postiz_integration_id_twitter",
    "linkedin": "postiz_integration_id_linkedin",
    "mastodon": "postiz_integration_id_mastodon",
    "reddit": "postiz_integration_id_reddit",
    "tiktok": "postiz_integration_id_tiktok",
    "instagram_reels": "postiz_integration_id_instagram",
}


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


class SocialDraftsService:
    async def create_draft(
        self,
        pipeline_task_id: str,
        platform: str,
        content: str,
        platform_config: dict[str, Any],
        pool: Any,
    ) -> str:
        """Insert a pending draft. Returns the new UUID string."""
        async with pool.acquire() as conn:
            new_id: str = await conn.fetchval(
                """
                INSERT INTO social_post_drafts
                    (pipeline_task_id, platform, content, platform_config)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text
                """,
                pipeline_task_id,
                platform,
                content,
                json.dumps(platform_config),
            )
        SOCIAL_DRAFT_CREATED_TOTAL.labels(platform=platform).inc()
        return new_id

    async def approve_draft(
        self,
        draft_id: str,
        pool: Any,
        site_config: SiteConfig,
    ) -> dict[str, Any]:
        """Approve a pending or failed draft: call Postiz immediately."""
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, platform, content, platform_config, status "
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
        client = PostizClient(base_url=base_url)

        result = await client.create_post(
            integration_id=integration_id,
            content=row["content"],
            platform_type=_PLATFORM_TYPE.get(platform) or platform,
            platform_settings=platform_config,
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
        conditions: list[str] = []
        args: list[Any] = []
        if post_id:
            args.append(post_id)
            conditions.append(f"post_id = ${len(args)}")
        if pipeline_task_id:
            args.append(pipeline_task_id)
            conditions.append(f"pipeline_task_id = ${len(args)}")
        if status:
            args.append(status)
            conditions.append(f"status = ${len(args)}")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM social_post_drafts {where} ORDER BY created_at DESC"
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


async def _mark_posted(draft_id: str, postiz_post_id: str | None, pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE social_post_drafts
            SET status = 'posted', postiz_post_id = $2, posted_at = now()
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
    )
