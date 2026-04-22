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
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from services.logger_config import get_logger
from utils.text_utils import extract_title_from_content

logger = get_logger(__name__)


# Strong references to fire-and-forget background tasks. Without this,
# asyncio may garbage-collect pending tasks before they run. See RUF006
# and https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro, name: str | None = None) -> asyncio.Task:
    """Schedule a coroutine as a fire-and-forget task, keeping a strong
    reference so asyncio doesn't collect it mid-run."""
    task = asyncio.ensure_future(coro)
    if name:
        # set_name is best-effort; some event-loop implementations don't
        # support it, but it's only used for debug visibility.
        with suppress(Exception):
            task.set_name(name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _write_text_file(path: str, content: str) -> None:
    """Small sync file-write suitable for ``asyncio.to_thread``. Avoids
    blocking the event loop on RSS feed regeneration in publish hot
    paths (ASYNC230)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class PublishResult:
    """Return value from publish_post_from_task."""

    def __init__(
        self,
        success: bool,
        post_id: str | None = None,
        post_slug: str | None = None,
        published_url: str | None = None,
        post_title: str | None = None,
        revalidation_success: bool = False,
        error: str | None = None,
    ):
        self.success = success
        self.post_id = post_id
        self.post_slug = post_slug
        self.published_url = published_url
        self.post_title = post_title
        self.revalidation_success = revalidation_success
        self.error = error

    def to_dict(self) -> dict[str, Any]:
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
    """Return True when running on local workstation (LOCAL_DATABASE_URL set).

    This is one of the ~8 legitimate bootstrap env vars per GH#93 — the
    literal presence of LOCAL_DATABASE_URL (vs cloud-only DATABASE_URL)
    is the signal that distinguishes local-workstation mode from cloud
    coordinator mode, before site_config exists. Cannot be migrated to
    DB-first without a circular bootstrap problem.
    """
    return bool(os.getenv("LOCAL_DATABASE_URL"))


async def _sync_published_post(post_id: str) -> None:
    """Push a newly published post to the cloud DB (non-blocking)."""
    if not _should_run_post_publish_hooks():
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


async def _ping_search_engines(
    site_url: str,
    post_url: str,
    site_config: Any,
) -> None:
    """Notify search engines about new content via IndexNow and Google ping."""
    import httpx

    # Tight caps on external SEO pings — they're fire-and-forget, we don't
    # want them to delay anything else if the target is slow.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0),
    ) as client:
        # IndexNow (Bing, Yandex, Naver, Seznam).
        # #198: both endpoint + key settings-backed. Setting the endpoint
        # to '' disables the ping without code changes.
        _indexnow_key = site_config.get("indexnow_key", "")
        _indexnow_url = site_config.get(
            "indexnow_ping_url", "https://api.indexnow.org/indexnow",
        )
        if _indexnow_url:
            try:
                await client.get(
                    _indexnow_url,
                    params={"url": post_url, "key": _indexnow_key},
                    timeout=10,
                )
                logger.info("[SEO] IndexNow ping sent for %s", post_url)
            except Exception as e:
                logger.debug("[SEO] IndexNow ping failed (non-fatal): %s", e)

        # Search-engine sitemap ping (Google's /ping endpoint by default;
        # set google_sitemap_ping_url='' to skip).
        _sitemap_ping = site_config.get(
            "google_sitemap_ping_url", "https://www.google.com/ping",
        )
        if _sitemap_ping:
            try:
                await client.get(
                    _sitemap_ping,
                    params={"sitemap": f"{site_url}/sitemap.xml"},
                    timeout=10,
                )
                logger.info("[SEO] Sitemap ping sent to %s", _sitemap_ping)
            except Exception as e:
                logger.debug("[SEO] Sitemap ping failed (non-fatal): %s", e)


async def _embed_published_post(db_service, post_dict: dict) -> None:
    """Embed a newly published post into pgvector (non-blocking)."""
    try:
        from plugins.registry import get_llm_providers
        from services.embedding_service import EmbeddingService

        embeddings_db = getattr(db_service, "embeddings", None)
        if not embeddings_db:
            logger.debug("[RAG] Skipping post embedding: embeddings DB not available")
            return

        # v2.2b: embed through the Provider Protocol — config-swappable
        # via plugin.llm_provider.primary.free in app_settings.
        providers = {p.name: p for p in get_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.debug("[RAG] Skipping post embedding: ollama_native provider not registered")
            return

        embedding_svc = EmbeddingService(provider=provider, embeddings_db=embeddings_db)
        await embedding_svc.embed_post(post_dict)
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


async def _calculate_scheduled_publish_time(db_service) -> datetime | None:
    """Determine when a post should be published to avoid batch-publishing.

    Reserved for a future scheduling/release-time-optimization feature. Callers
    can opt in by passing honor_pacing=True to publish_post_from_task; today
    the default is False (immediate publish) because human approval is the
    gate — pacing the output of a human reviewer is redundant.

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

    pool = getattr(db_service, "cloud_pool", None) or db_service.pool
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
    task: dict[str, Any],
    task_id: str,
    *,
    site_config: Any,
    publisher: str = "operator",
    trigger_revalidation: bool = True,
    queue_social: bool = True,
    draft_mode: bool = False,
    honor_pacing: bool = False,
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
    # gitea#268: strip any stray HTML (notably <img> tags) from the SEO
    # description before it ships as the post's excerpt + meta description.
    # Upstream writers occasionally leak markup that the /posts cards then
    # render as literal text.
    if seo_description:
        seo_description = re.sub(r"<[^>]+>", "", seo_description).strip()
        seo_description = re.sub(r"\s+", " ", seo_description)
    seo_keywords = merged.get("seo_keywords", [])
    featured_image_url = merged.get("featured_image_url") or task.get("featured_image_url")
    metadata = merged.get("metadata", {})

    if not draft_content or not topic:
        msg = "Missing content or topic — cannot create post"
        logger.warning("[publish_service] %s for task %s", msg, task_id)
        return PublishResult(success=False, error=msg)

    # ---------------------------------------------------------------
    # 1b. Idempotency guard — prevent duplicate posts for the same task
    #     Slugs contain task_id[:8] suffix, so we can match on that.
    # ---------------------------------------------------------------
    _task_suffix = task_id[:8]
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool
    existing = await pool.fetchrow(
        "SELECT id, slug, title FROM posts WHERE slug LIKE '%' || $1", _task_suffix
    )
    if existing:
        logger.warning(
            "[publish_service] Post already exists for task %s (post_id=%s, slug=%s) — skipping duplicate",
            task_id, existing["id"], existing["slug"],
        )
        return PublishResult(
            success=True,
            post_id=str(existing["id"]),
            post_slug=existing["slug"],
            published_url=f"/posts/{existing['slug']}",
            post_title=existing.get("title", topic),
        )

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
    from services.category_resolver import select_category_for_topic
    from services.default_author import get_or_create_default_author

    author_id = await get_or_create_default_author(db_service)
    category_id = await select_category_for_topic(post_title, db_service)

    # ---------------------------------------------------------------
    # 4c. Resolve tags (gitea#267) — derive post.tag_ids from the task's
    # submitted tags + seo_keywords, upsert into the `tags` table so new
    # terms auto-create, and pass to content_db.create_post which will
    # populate post_tags junction. Empty tags → no-op (downstream code
    # tolerates missing tag_ids).
    # ---------------------------------------------------------------
    candidate_tag_strings: list[str] = []
    # Task-level tags (now threaded via ModelConverter.to_task_response
    # since gitea#270 fix — pulls from metadata JSONB as fallback).
    task_tags = task.get("tags") or merged.get("tags") or []
    if isinstance(task_tags, str):
        task_tags = [t.strip() for t in task_tags.split(",") if t.strip()]
    candidate_tag_strings.extend(task_tags or [])
    # SEO keywords as a fallback source (already a list here; the
    # str-join happens later when writing posts.seo_keywords).
    if isinstance(seo_keywords, list):
        candidate_tag_strings.extend(seo_keywords)
    elif isinstance(seo_keywords, str) and seo_keywords:
        candidate_tag_strings.extend(
            t.strip() for t in seo_keywords.split(",") if t.strip()
        )

    tag_ids: list[str] = []
    if candidate_tag_strings:
        # Normalize to slug form: lowercase, hyphenated, alnum-only.
        seen_slugs: set[str] = set()
        clean_pairs: list[tuple[str, str]] = []  # (slug, display_name)
        for raw in candidate_tag_strings:
            if not raw or not isinstance(raw, str):
                continue
            name = raw.strip()
            if not name:
                continue
            slug_form = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not slug_form or slug_form in seen_slugs:
                continue
            seen_slugs.add(slug_form)
            clean_pairs.append((slug_form, name))
        if clean_pairs:
            try:
                async with pool.acquire() as conn:
                    for slug_form, display_name in clean_pairs:
                        row = await conn.fetchrow(
                            """
                            INSERT INTO tags (name, slug)
                            VALUES ($1, $2)
                            ON CONFLICT (slug) DO UPDATE SET updated_at = NOW()
                            RETURNING id
                            """,
                            display_name, slug_form,
                        )
                        if row:
                            tag_ids.append(str(row["id"]))
                logger.info(
                    "[publish_service] Resolved %d tag_ids for task %s: %s",
                    len(tag_ids), task_id, [p[0] for p in clean_pairs],
                )
            except Exception as tag_err:
                # Tag resolution must never block publishing.
                logger.warning(
                    "[publish_service] Tag resolution failed for task %s: %s",
                    task_id, tag_err,
                )
                tag_ids = []

    # ---------------------------------------------------------------
    # 4b. Determine scheduled publish time (content spacing)
    # ---------------------------------------------------------------
    # Pacing is reserved for a future scheduling feature. When honor_pacing
    # is False (default), the post publishes immediately because the human
    # reviewer is already the gate and pacing their output is redundant.
    scheduled_at = (
        await _calculate_scheduled_publish_time(db_service) if honor_pacing else None
    )
    if scheduled_at:
        logger.info(
            "[publish_service] Post scheduled for %s (not immediate)",
            scheduled_at.isoformat(),
        )

    # ---------------------------------------------------------------
    # 5. Insert into posts table
    # ---------------------------------------------------------------
    post_data: dict[str, Any] = {
        "title": post_title,
        "slug": slug,
        "content": post_content,
        "excerpt": seo_description,
        "featured_image_url": featured_image_url,
        "cover_image_url": featured_image_url,
        "author_id": author_id,
        "category_id": category_id,
        "status": "draft" if draft_mode else "published",
        "seo_title": post_title,
        "seo_description": seo_description,
        "seo_keywords": ", ".join(seo_keywords) if isinstance(seo_keywords, list) else (seo_keywords or ""),
        "metadata": metadata,
        "tag_ids": tag_ids or None,
    }
    if scheduled_at:
        post_data["published_at"] = scheduled_at

    # Mark non-draft posts as eligible for feed distribution
    if not draft_mode:
        post_data["distributed_at"] = scheduled_at or datetime.now(timezone.utc)

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

        await emit_webhook_event(getattr(db_service, "cloud_pool", None) or db_service.pool, "post.published", {
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
            _spawn_background(
                _sync_published_post(post_id), name=f"sync_published_post({post_id})"
            )
            _spawn_background(
                _embed_published_post(db_service, post_dict),
                name=f"embed_published_post({post_id})",
            )
        logger.info("[publish_service] Queued sync + embed for post %s", post_id)

    # ---------------------------------------------------------------
    # 9. Queue social media post generation
    # ---------------------------------------------------------------
    if queue_social:
        try:
            from services.social_poster import generate_and_distribute_social_posts
            # publish_service still reads the module singleton pending
            # its own Phase H migration; pass it through so social_poster
            # no longer reaches for it at module scope.
            from services.site_config import site_config as _sc

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
                        site_config=_sc,
                    )
                else:
                    _spawn_background(
                        generate_and_distribute_social_posts(
                            title=_title, slug=slug,
                            excerpt=seo_description, keywords=_seo_kw,
                            site_config=_sc,
                        ),
                        name=f"social_posts({slug})",
                    )
                logger.info("[SOCIAL] Queued social post generation for %s", slug)
        except Exception as e:
            logger.debug("[SOCIAL] Social posting failed (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 9b. Queue Dev.to cross-posting (fire-and-forget)
    # ---------------------------------------------------------------
    try:
        from services.devto_service import DevToCrossPostService

        devto_svc = DevToCrossPostService(
            getattr(db_service, "cloud_pool", None) or db_service.pool,
            site_config,
        )
        if background_tasks:
            background_tasks.add_task(
                devto_svc.cross_post_by_post_id, post_id
            )
        else:
            _spawn_background(
                devto_svc.cross_post_by_post_id(post_id),
                name=f"devto_crosspost({post_id})",
            )
        logger.info("[DEVTO] Queued cross-post for post %s", post_id)
    except Exception as e:
        logger.debug("[DEVTO] Cross-posting setup failed (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 10. ISR revalidation
    # ---------------------------------------------------------------
    revalidation_success = False
    if trigger_revalidation:
        try:
            from services.revalidation_service import trigger_nextjs_revalidation

            reval_paths = ["/", "/archive", "/posts", f"/posts/{slug}"]
            reval_tags = ["posts", "post-index", f"post:{slug}"]
            revalidation_success = await trigger_nextjs_revalidation(
                reval_paths, reval_tags, site_config=site_config,
            )
            if not revalidation_success:
                logger.warning("[publish_service] ISR revalidation returned failure for %s", slug)
        except Exception as reval_err:
            logger.warning("[publish_service] ISR revalidation error (non-fatal): %s", reval_err)

    # ---------------------------------------------------------------
    # 10b. Static JSON export to CDN (fire-and-forget)
    # ---------------------------------------------------------------
    try:
        from services.static_export_service import export_post

        _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
        if background_tasks:
            background_tasks.add_task(export_post, _pool, slug)
        else:
            _spawn_background(
                export_post(_pool, slug), name=f"static_export({slug})"
            )
        logger.info("[STATIC_EXPORT] Queued export for %s", slug)
    except Exception as e:
        logger.debug("[STATIC_EXPORT] Failed to queue export (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 11. Ping search engines (fire-and-forget)
    # ---------------------------------------------------------------
    site_url = site_config.require("site_url")
    published_url_full = f"{site_url}/posts/{slug}"
    _spawn_background(
        _ping_search_engines(site_url, published_url_full, site_config),
        name=f"ping_search_engines({slug})",
    )

    # ---------------------------------------------------------------
    # 11b. Generate podcast episode (fire-and-forget, local worker only)
    # ---------------------------------------------------------------
    _pre_script = merged.get("podcast_script") or ""
    _video_scenes = merged.get("video_scenes") or []
    _short_summary = merged.get("short_summary_script") or ""
    if _should_run_post_publish_hooks():
        try:
            from services.podcast_service import generate_podcast_episode

            if background_tasks:
                background_tasks.add_task(
                    generate_podcast_episode, post_id, post_title, post_content,
                    site_config=site_config,
                    pre_generated_script=_pre_script,
                )
            else:
                _spawn_background(
                    generate_podcast_episode(post_id, post_title, post_content,
                                            site_config=site_config,
                                            pre_generated_script=_pre_script),
                    name=f"podcast_episode({post_id})",
                )
            logger.info("[PODCAST] Queued episode generation for post %s", post_id)
        except Exception as e:
            logger.debug("[PODCAST] Failed to queue episode (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 11c. Generate video episode (fire-and-forget, local worker only)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        try:
            from services.video_service import generate_video_episode

            if background_tasks:
                background_tasks.add_task(
                    generate_video_episode, post_id, post_title, post_content,
                    site_config=site_config,
                    pre_generated_scenes=_video_scenes,
                )
            else:
                _spawn_background(
                    generate_video_episode(post_id, post_title, post_content,
                                          site_config=site_config,
                                          pre_generated_scenes=_video_scenes),
                    name=f"video_episode({post_id})",
                )
            logger.info("[VIDEO] Queued video generation for post %s", post_id)
        except Exception as e:
            logger.debug("[VIDEO] Failed to queue video (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 11d. Generate short-form video (fire-and-forget, local worker only)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        try:
            from services.video_service import generate_short_video_for_post

            async def _gen_short(pid, ptitle, pcontent, scenes, short_script):
                """Wait for podcast, then generate short video."""
                import asyncio as _aio

                _delay = int(
                    site_config.get("short_video_post_publish_delay_seconds", "180"),
                )
                await _aio.sleep(_delay)
                try:
                    result = await generate_short_video_for_post(
                        pid, ptitle, pcontent,
                        pre_generated_scenes=scenes,
                        pre_generated_summary=short_script,
                        site_config=site_config,
                    )
                    if not result.success:
                        logger.warning("[SHORT] Failed for post %s: %s", pid, result.error)
                except Exception as e:
                    logger.warning("[SHORT] Unexpected error for post %s: %s", pid, e)

            _spawn_background(
                _gen_short(post_id, post_title, post_content, _video_scenes, _short_summary),
                name=f"short_video({post_id})",
            )
            logger.info("[SHORT] Queued short video generation for post %s", post_id)
        except Exception as e:
            logger.debug("[SHORT] Failed to queue short video (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 11e. Upload media to R2 CDN (fire-and-forget, after generation)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        async def _upload_media_to_r2(pid: str) -> None:
            """Wait for media files to appear, then upload to R2."""
            import asyncio as _aio
            from pathlib import Path

            from services.r2_upload_service import (
                upload_podcast_episode,
                upload_to_r2,
                upload_video_episode,
            )
            # Give podcast/video/short generation time to complete
            _delay = int(site_config.get("media_r2_upload_delay_seconds", "240"))
            await _aio.sleep(_delay)
            await upload_podcast_episode(pid, site_config=_scfg)
            await upload_video_episode(pid, site_config=_scfg)
            # Upload short video if it exists
            short_path = Path(os.path.expanduser("~")) / ".poindexter" / "video" / f"{pid}-short.mp4"
            if short_path.exists():
                await upload_to_r2(
                    str(short_path),
                    f"video/{pid}-short.mp4",
                    "video/mp4",
                    site_config=_scfg,
                )
            # Regenerate public podcast RSS feed on R2
            try:
                import httpx as _hx

                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                _api_base = site_config.get(
                    "internal_api_base_url", DEFAULT_WORKER_API_URL,
                )
                async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=5.0)) as _client:
                    _feed = await _client.get(f"{_api_base}/api/podcast/feed.xml", timeout=30)
                    _feed_path = "/tmp/podcast-feed.xml"
                    # Blocking file I/O in async context — push to worker thread
                    # so the event loop isn't stalled while we write the feed file.
                    await asyncio.to_thread(
                        _write_text_file, _feed_path, _feed.text,
                    )
                    await upload_to_r2(
                        _feed_path,
                        "podcast/feed.xml",
                        "application/rss+xml",
                        site_config=_scfg,
                    )
                    logger.info("[R2] Podcast RSS feed regenerated on CDN")
            except Exception as _e:
                logger.warning("[R2] Podcast feed regen failed (non-fatal): %s", _e)

            # Regenerate public video RSS feed on R2
            try:
                import httpx as _hx

                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                _api_base = site_config.get(
                    "internal_api_base_url", DEFAULT_WORKER_API_URL,
                )
                async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=5.0)) as _client:
                    _feed = await _client.get(f"{_api_base}/api/video/feed.xml", timeout=30)
                    _feed_path = "/tmp/video-feed.xml"
                    await asyncio.to_thread(
                        _write_text_file, _feed_path, _feed.text,
                    )
                    await upload_to_r2(
                        _feed_path,
                        "video/feed.xml",
                        "application/rss+xml",
                        site_config=_scfg,
                    )
                    logger.info("[R2] Video RSS feed regenerated on CDN")
            except Exception as _e:
                logger.warning("[R2] Video feed regen failed (non-fatal): %s", _e)

            # Upload video to YouTube if enabled
            try:
                _platforms = site_config.get("social_distribution_platforms", "")
                if "youtube" in _platforms:
                    video_path = Path(os.path.expanduser("~")) / ".poindexter" / "video" / f"{pid}.mp4"
                    if video_path.exists():
                        from services.social_adapters.youtube import upload_to_youtube
                        # Get post details for YouTube metadata
                        _post = await db_service.pool.fetchrow(
                            "SELECT title, excerpt, seo_keywords, slug FROM posts WHERE id::text = $1",
                            pid,
                        ) if db_service and db_service.pool else None
                        _yt_title = _post["title"] if _post else "Poindexter Video"
                        _yt_slug = _post["slug"] if _post else ""
                        _site_url = site_config.get("site_url", "https://www.gladlabs.io")
                        _yt_desc = (
                            f"{_post['excerpt'] or ''}\n\n"
                            f"Read the full article: {_site_url}/posts/{_yt_slug}\n\n"
                            f"Generated by Poindexter — open-source AI content pipeline\n"
                            f"https://github.com/Glad-Labs/poindexter"
                        ) if _post else ""
                        _yt_tags = [t.strip() for t in (_post.get("seo_keywords") or "").split(",") if t.strip()][:10] if _post else []

                        _yt_result = await upload_to_youtube(
                            video_path=str(video_path),
                            title=_yt_title,
                            description=_yt_desc,
                            tags=_yt_tags,
                        )
                        if _yt_result.get("success"):
                            logger.info("[YOUTUBE] Uploaded: %s", _yt_result.get("url", ""))
                        else:
                            logger.warning("[YOUTUBE] Upload failed (non-fatal): %s", _yt_result.get("error", ""))
                    else:
                        logger.debug("[YOUTUBE] No video file for %s, skipping", pid)
            except Exception as _e:
                logger.warning("[YOUTUBE] Upload error (non-fatal): %s", _e)

        _spawn_background(
            _upload_media_to_r2(post_id), name=f"upload_media_r2({post_id})"
        )

    # ---------------------------------------------------------------
    # 11f. Newsletter to subscribers (fire-and-forget)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        async def _send_newsletter(_pid: str, ptitle: str, pexcerpt: str, pslug: str) -> None:
            try:
                from services.newsletter_service import send_post_newsletter
                _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
                result = await send_post_newsletter(
                    _pool, ptitle, pexcerpt, pslug, site_config,
                )
                logger.info("[NEWSLETTER] Result: %s", result)
            except Exception as e:
                logger.debug("[NEWSLETTER] Failed (non-fatal): %s", e)

        _spawn_background(
            _send_newsletter(post_id, post_title, seo_description, slug),
            name=f"send_newsletter({post_id})",
        )

    # ---------------------------------------------------------------
    # 12. Send notification
    # ---------------------------------------------------------------
    try:
        from services.task_executor import _notify_openclaw

        _q_score = task.get("quality_score") or merged.get("quality_score") or "N/A"
        await _notify_openclaw(
            f"Published: {post_title}\n/posts/{slug}\nScore: {_q_score}",
            critical=True,
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
