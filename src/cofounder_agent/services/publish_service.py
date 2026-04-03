"""
Shared Post Publishing Service

Single source of truth for creating a published post from a completed content task.
Called by:
  1. /approve endpoint (auto_publish=True)  - task_publishing_routes.py
  2. /publish endpoint                       - task_publishing_routes.py
  3. _auto_publish_task in TaskExecutor       - task_executor.py

This eliminates three divergent copy-paste blocks that were drifting apart.
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from services.logger_config import get_logger
from utils.text_utils import extract_title_from_content

logger = get_logger(__name__)


class PublishResult:
    """Return value from publish_post_from_task."""

    def __init__(
        self,
        success: bool,
        post_id: Optional[str] = None,
        post_slug: Optional[str] = None,
        published_url: Optional[str] = None,
        post_title: Optional[str] = None,
        revalidation_success: bool = False,
        error: Optional[str] = None,
    ):
        self.success = success
        self.post_id = post_id
        self.post_slug = post_slug
        self.published_url = published_url
        self.post_title = post_title
        self.revalidation_success = revalidation_success
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "post_id": self.post_id,
            "post_slug": self.post_slug,
            "published_url": self.published_url,
            "post_title": self.post_title,
            "revalidation_success": self.revalidation_success,
            "error": self.error,
        }


def _should_run_post_publish_hooks() -> bool:
    """Return True when running on local workstation (LOCAL_DATABASE_URL set)."""
    return bool(os.getenv("LOCAL_DATABASE_URL"))


async def _sync_published_post(post_id: str) -> None:
    """Push a newly published post to the cloud Railway DB (non-blocking)."""
    if not os.getenv("LOCAL_DATABASE_URL"):
        return
    try:
        from services.sync_service import SyncService

        async with SyncService() as sync:
            ok = await sync.push_post(post_id)
            if ok:
                logger.info("[SYNC] Pushed published post to cloud DB: %s", post_id)
            else:
                logger.warning("[SYNC] push_post returned False for post %s", post_id)
    except Exception as e:
        logger.warning("[SYNC] Failed to sync published post (non-fatal): %s", e)


async def _ping_search_engines(site_url: str, post_url: str) -> None:
    """Notify search engines about new content via IndexNow and Google ping."""
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        # IndexNow (Bing, Yandex, Naver, Seznam)
        try:
            await client.get(
                "https://api.indexnow.org/indexnow",
                params={"url": post_url, "key": "gladlabs"},
            )
            logger.info("[SEO] IndexNow ping sent for %s", post_url)
        except Exception as e:
            logger.debug("[SEO] IndexNow ping failed (non-fatal): %s", e)

        # Google ping (sitemap-based)
        try:
            await client.get(
                "https://www.google.com/ping",
                params={"sitemap": f"{site_url}/sitemap.xml"},
            )
            logger.info("[SEO] Google sitemap ping sent")
        except Exception as e:
            logger.debug("[SEO] Google ping failed (non-fatal): %s", e)


async def _embed_published_post(db_service, post_dict: dict) -> None:
    """Embed a newly published post into pgvector (non-blocking)."""
    try:
        from services.ollama_client import OllamaClient
        from services.embedding_service import EmbeddingService

        embeddings_db = getattr(db_service, "embeddings", None)
        if not embeddings_db:
            logger.debug("[RAG] Skipping post embedding: embeddings DB not available")
            return

        ollama = OllamaClient()
        embedding_svc = EmbeddingService(ollama_client=ollama, embeddings_db=embeddings_db)
        await embedding_svc.embed_post(post_dict)
        await ollama.close()
        logger.info("[RAG] Embedded published post for future RAG: %s", post_dict.get("title", "")[:60])
    except Exception as e:
        logger.warning("[RAG] Failed to embed published post (non-fatal): %s", e)


def _parse_json_field(value, field_name: str = "field", task_id: str = "") -> dict:
    """Safely parse a JSON string field into a dict."""
    if isinstance(value, str):
        try:
            return json.loads(value) if value else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("[publish_service] %s is not valid JSON for task %s", field_name, task_id)
            return {}
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    return value


async def _calculate_scheduled_publish_time(db_service) -> Optional[datetime]:
    """Determine when a post should be published to avoid batch-publishing.

    Checks how many posts were already published today and what the latest
    publish time is.  If the daily cap is reached, the post is scheduled for
    the next available day.  Within a day, posts are spaced by at least
    ``publish_spacing_hours`` (default 4 h).

    Returns:
        ``None`` if the post can be published right now (NOW()), or a future
        ``datetime`` (UTC) for the scheduled slot.
    """
    try:
        max_per_day = int(await db_service.get_setting_value("max_posts_per_day", 3))
        spacing_hours = int(await db_service.get_setting_value("publish_spacing_hours", 4))
    except Exception:
        # If settings lookup fails, publish immediately — don't block the pipeline
        logger.debug("[schedule] Could not read scheduling settings, publishing now")
        return None

    pool = db_service.pool
    try:
        async with pool.acquire() as conn:
            # How many posts published today (by published_at date, UTC)?
            row = await conn.fetchrow(
                """
                SELECT COUNT(*)                          AS cnt,
                       MAX(published_at)                 AS latest
                FROM   posts
                WHERE  status = 'published'
                  AND  published_at::date = CURRENT_DATE
                """
            )
    except Exception as e:
        logger.debug("[schedule] DB query failed, publishing now: %s", e)
        return None

    today_count = row["cnt"] if row else 0
    latest_today = row["latest"] if row else None

    now = datetime.now(timezone.utc)

    # --- Under the daily cap: check spacing ---
    if today_count < max_per_day:
        if latest_today is not None:
            # Ensure latest_today is timezone-aware
            if latest_today.tzinfo is None:
                latest_today = latest_today.replace(tzinfo=timezone.utc)
            earliest_next = latest_today + timedelta(hours=spacing_hours)
            if earliest_next > now:
                # Still within today — schedule at the spaced time
                if earliest_next.date() == now.date():
                    logger.info(
                        "[schedule] Spacing: scheduling post at %s (last was %s)",
                        earliest_next.isoformat(), latest_today.isoformat(),
                    )
                    return earliest_next
                # Spacing pushes into tomorrow — fall through to next-day logic
            else:
                # Enough time has passed since last publish — go now
                return None
        else:
            # No posts today yet — publish immediately
            return None

    # --- Daily cap reached: schedule for the next available day ---
    # Find the next day that has fewer than max_per_day posts
    check_date = now.date() + timedelta(days=1)
    for _ in range(30):  # look up to 30 days ahead (safety bound)
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*)     AS cnt,
                           MAX(published_at) AS latest
                    FROM   posts
                    WHERE  status = 'published'
                      AND  published_at::date = $1
                    """,
                    check_date,
                )
        except Exception:
            break

        day_count = row["cnt"] if row else 0
        if day_count < max_per_day:
            # Pick a random hour between 8 AM and 6 PM UTC for a natural look
            hour = random.randint(8, 17)
            minute = random.randint(0, 59)

            # If there are already posts that day, respect spacing
            day_latest = row["latest"] if row else None
            scheduled = datetime(
                check_date.year, check_date.month, check_date.day,
                hour, minute, 0, tzinfo=timezone.utc,
            )
            if day_latest is not None:
                if day_latest.tzinfo is None:
                    day_latest = day_latest.replace(tzinfo=timezone.utc)
                earliest = day_latest + timedelta(hours=spacing_hours)
                if scheduled < earliest:
                    scheduled = earliest
                    # Nudge into the 8-18 window if spacing pushed it earlier
                    if scheduled.hour < 8:
                        scheduled = scheduled.replace(hour=8, minute=random.randint(0, 59))

            logger.info(
                "[schedule] Daily cap reached (%d/%d). Scheduling post for %s",
                today_count, max_per_day, scheduled.isoformat(),
            )
            return scheduled
        check_date += timedelta(days=1)

    # Fallback: couldn't find a slot (extremely unlikely) — publish now
    logger.warning("[schedule] Could not find an open slot in 30 days, publishing now")
    return None


async def publish_post_from_task(
    db_service,
    task: Dict[str, Any],
    task_id: str,
    *,
    publisher: str = "operator",
    trigger_revalidation: bool = True,
    queue_social: bool = True,
    background_tasks=None,
) -> PublishResult:
    """Create a published post from a completed content task.

    This is the ONE place where a task becomes a post. All code paths
    (approve auto-publish, explicit /publish, worker auto-publish) call this.

    Args:
        db_service: DatabaseService instance
        task: Full task dict (from db_service.get_task)
        task_id: Task UUID string
        publisher: Who triggered the publish (for audit trail)
        trigger_revalidation: Whether to trigger ISR revalidation
        queue_social: Whether to queue social media post generation
        background_tasks: Optional FastAPI BackgroundTasks for non-blocking work

    Returns:
        PublishResult with post details or error info
    """
    from utils.json_encoder import convert_decimals, safe_json_dumps

    # ---------------------------------------------------------------
    # 1. Parse task data (result + task_metadata merged)
    # ---------------------------------------------------------------
    task_result = _parse_json_field(task.get("result"), "result", task_id)
    task_metadata = _parse_json_field(task.get("task_metadata"), "task_metadata", task_id)
    # task_result wins over task_metadata
    merged = {**task_metadata, **task_result}

    topic = task.get("topic", "") or merged.get("topic", "")
    draft_content = (
        task.get("content", "")
        or merged.get("draft_content", "")
        or merged.get("content", "")
        or merged.get("body", "")
        or merged.get("article", "")
        or ""
    )
    seo_description = merged.get("seo_description", "") or task.get("seo_description", "")
    seo_keywords = merged.get("seo_keywords", [])
    featured_image_url = merged.get("featured_image_url") or task.get("featured_image_url")
    metadata = merged.get("metadata", {})

    if not draft_content or not topic:
        msg = "Missing content or topic — cannot create post"
        logger.warning("[publish_service] %s for task %s", msg, task_id)
        return PublishResult(success=False, error=msg)

    # ---------------------------------------------------------------
    # 2. Extract title from content (LLM often puts # Title at top)
    # ---------------------------------------------------------------
    extracted_title, cleaned_content = extract_title_from_content(draft_content)
    post_title = extracted_title or merged.get("title") or topic
    post_content = cleaned_content

    logger.info("[publish_service] Post title: %s", post_title)
    logger.info("[publish_service] Extracted from content: %s", bool(extracted_title))
    logger.info("[publish_service] Content length: %d chars", len(post_content or ""))

    # ---------------------------------------------------------------
    # 3. Create slug
    # ---------------------------------------------------------------
    slug = re.sub(r"[^\w\s-]", "", post_title).lower().replace(" ", "-")[:50]
    slug = f"{slug}-{task_id[:8]}"

    # ---------------------------------------------------------------
    # 4. Get author + category
    # ---------------------------------------------------------------
    from services.content_router_service import (
        _get_or_create_default_author,
        _select_category_for_topic,
    )

    author_id = await _get_or_create_default_author(db_service)
    category_id = await _select_category_for_topic(post_title, db_service)

    # ---------------------------------------------------------------
    # 4b. Determine scheduled publish time (content spacing)
    # ---------------------------------------------------------------
    scheduled_at = await _calculate_scheduled_publish_time(db_service)
    if scheduled_at:
        logger.info(
            "[publish_service] Post scheduled for %s (not immediate)",
            scheduled_at.isoformat(),
        )

    # ---------------------------------------------------------------
    # 5. Insert into posts table
    # ---------------------------------------------------------------
    post_data: Dict[str, Any] = {
        "title": post_title,
        "slug": slug,
        "content": post_content,
        "excerpt": seo_description,
        "featured_image_url": featured_image_url,
        "author_id": author_id,
        "category_id": category_id,
        "status": "published",
        "seo_title": post_title,
        "seo_description": seo_description,
        "seo_keywords": ",".join(seo_keywords) if seo_keywords else "",
        "metadata": metadata,
    }
    if scheduled_at:
        post_data["published_at"] = scheduled_at

    try:
        post = await db_service.create_post(post_data)
    except Exception as e:
        msg = f"Failed to create post: {type(e).__name__}: {e}"
        logger.error("[publish_service] %s", msg, exc_info=True)
        return PublishResult(success=False, error=msg)

    post_id = str(post.id) if hasattr(post, "id") else str(post.get("id", ""))  # type: ignore[union-attr]
    logger.info("[publish_service] Post created: id=%s slug=%s", post_id, slug)
    logger.info(
        "[content_published] task_id=%s post_id=%s user_id=%s slug=%s",
        task_id, post_id, publisher, slug,
    )

    # ---------------------------------------------------------------
    # 6. Update task result with post info
    # ---------------------------------------------------------------
    merged["post_id"] = post_id
    merged["post_slug"] = slug
    merged["published_url"] = f"/posts/{slug}"

    publish_meta = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "published_by": publisher,
    }
    if scheduled_at:
        publish_meta["scheduled_publish_at"] = scheduled_at.isoformat()
    merged["publish_metadata"] = publish_meta

    try:
        await db_service.update_task_status(
            task_id, "published", result=safe_json_dumps(convert_decimals(merged))
        )
    except Exception as e:
        logger.warning("[publish_service] Failed to update task result: %s", e)

    # ---------------------------------------------------------------
    # 7. Emit webhook
    # ---------------------------------------------------------------
    try:
        from services.webhook_delivery_service import emit_webhook_event

        await emit_webhook_event(db_service.pool, "post.published", {
            "task_id": str(task_id), "title": post_title, "site": "default",
        })
    except Exception:
        logger.debug("[WEBHOOK] Failed to emit post.published event", exc_info=True)

    # ---------------------------------------------------------------
    # 8. Sync to cloud DB + embed in pgvector
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        post_dict = {
            "id": post_id,
            "title": post_title,
            "excerpt": seo_description,
            "content": post_content,
        }
        if background_tasks:
            background_tasks.add_task(_sync_published_post, post_id)
            background_tasks.add_task(_embed_published_post, db_service, post_dict)
        else:
            asyncio.ensure_future(_sync_published_post(post_id))
            asyncio.ensure_future(_embed_published_post(db_service, post_dict))
        logger.info("[publish_service] Queued sync + embed for post %s", post_id)

    # ---------------------------------------------------------------
    # 9. Queue social media post generation
    # ---------------------------------------------------------------
    if queue_social:
        try:
            from services.social_poster import generate_and_distribute_social_posts

            _title = task.get("title") or task.get("topic") or post_title
            _seo_kw = seo_keywords
            if isinstance(_seo_kw, str):
                _seo_kw = [k.strip() for k in _seo_kw.split(",") if k.strip()]
            if _title and slug:
                if background_tasks:
                    background_tasks.add_task(
                        generate_and_distribute_social_posts,
                        title=_title, slug=slug,
                        excerpt=seo_description, keywords=_seo_kw,
                    )
                else:
                    asyncio.ensure_future(
                        generate_and_distribute_social_posts(
                            title=_title, slug=slug,
                            excerpt=seo_description, keywords=_seo_kw,
                        )
                    )
                logger.info("[SOCIAL] Queued social post generation for %s", slug)
        except Exception as e:
            logger.debug("[SOCIAL] Social posting failed (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 10. ISR revalidation
    # ---------------------------------------------------------------
    revalidation_success = False
    if trigger_revalidation:
        try:
            from routes.revalidate_routes import trigger_nextjs_revalidation

            reval_paths = ["/", "/archive", "/posts", f"/posts/{slug}"]
            revalidation_success = await trigger_nextjs_revalidation(reval_paths)
            if not revalidation_success:
                logger.warning("[publish_service] ISR revalidation returned failure for %s", slug)
        except Exception as reval_err:
            logger.warning("[publish_service] ISR revalidation error (non-fatal): %s", reval_err)

    # ---------------------------------------------------------------
    # 11. Ping search engines (fire-and-forget)
    # ---------------------------------------------------------------
    site_url = "https://www.gladlabs.io"
    published_url_full = f"{site_url}/posts/{slug}"
    asyncio.ensure_future(_ping_search_engines(site_url, published_url_full))

    # ---------------------------------------------------------------
    # 12. Send notification
    # ---------------------------------------------------------------
    try:
        from services.task_executor import _notify_openclaw

        await _notify_openclaw(
            f"Published: {post_title}\n/posts/{slug}\nScore: {merged.get('quality_score', 'N/A')}"
        )
    except Exception:
        logger.debug("[publish_service] Notification failed (non-fatal)", exc_info=True)

    return PublishResult(
        success=True,
        post_id=post_id,
        post_slug=slug,
        published_url=f"/posts/{slug}",
        post_title=post_title,
        revalidation_success=revalidation_success,
    )
