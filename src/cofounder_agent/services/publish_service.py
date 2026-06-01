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
import tempfile
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.text_utils import extract_title_from_content

# #272 Phase-2g: the module-level ``site_config`` global + ``set_site_config``
# setter (and the ``_resolve_site_config`` fallback shim) are DELETED.
# injection is now mandatory — the public entries (``publish_post_from_task`` /
# ``fire_post_distribution_hooks``) and the internal ``_ping_search_engines``
# all take a REQUIRED keyword ``site_config`` and callers thread the run-bound
# instance (routes via ``Depends(get_site_config_dependency)``; the Prefect
# post-pipeline auto-publish path via its threaded SiteConfig; the
# scheduled-publisher / idle-worker via their wired instance).
# ``publish_service`` is removed from ``di_wiring.WIRED_MODULES``.


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
        static_export_success: bool = False,
        staged: bool = False,
        error: str | None = None,
    ):
        self.success = success
        self.post_id = post_id
        self.post_slug = post_slug
        self.published_url = published_url
        self.post_title = post_title
        self.revalidation_success = revalidation_success
        self.static_export_success = static_export_success
        # True when the post was created at status='approved' for
        # later scheduling (stage_only mode in publish_post_from_task).
        # Callers that distinguish staged vs live publishes (e.g.
        # operator notify, social queue) check this field to skip
        # publish-only side-effects.
        self.staged = staged
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "post_id": self.post_id,
            "post_slug": self.post_slug,
            "published_url": self.published_url,
            "post_title": self.post_title,
            "revalidation_success": self.revalidation_success,
            "static_export_success": self.static_export_success,
            "staged": self.staged,
            "error": self.error,
        }


def _should_run_post_publish_hooks() -> bool:
    """Return True when running on the local-workstation worker.

    Post-publish hooks (podcast / video / R2 / RSS / YouTube / newsletter)
    only make sense on the worker that owns the local content pipeline
    + GPU + filesystem. The coordinator mode never runs these.

    Reads ``DEPLOYMENT_MODE`` directly from the environment because this
    helper fires from within the publish path long after bootstrap has
    completed — site_config is available, but using DEPLOYMENT_MODE keeps
    this consistent with how ``main.py`` decides which mode to start in.

    Until 2026-05-08 this read ``LOCAL_DATABASE_URL`` instead of
    ``DEPLOYMENT_MODE`` — a stale signal that no container actually sets,
    which silently disabled all six post-publish hooks. The 8-day dark
    distribution-paths regression fixed in commit ``<this commit>``.
    """
    return os.getenv("DEPLOYMENT_MODE", "coordinator").lower() == "worker"


async def _post_has_pending_gates(pool, post_id: str) -> bool:
    """Return True iff the post has any unresolved gate row.

    Used by the publish path to decide whether to fire the
    distribution + media-generation hooks immediately or defer them
    until the operator clears the gates (Glad-Labs/poindexter#24).

    Back-compat: posts that pre-date the gate engine (no rows in
    ``post_approval_gates``) have zero pending gates and so the
    distribution hooks fire as before.

    Defensive: any DB error returns False so we err on the side of
    publishing — the alternative (silently swallowing distribution
    for every post on a transient DB blip) is worse.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1
                  FROM post_approval_gates
                 WHERE post_id::text = $1
                   AND state = 'pending'
                 LIMIT 1
                """,
                str(post_id),
            )
        return row is not None
    except Exception as exc:
        logger.debug(
            "[publish_service] _post_has_pending_gates probe failed "
            "for %s (treating as no gates): %s",
            post_id, exc,
        )
        return False


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
                # 2026-05-12 fail-loud sweep: emit a finding so the
                # operator sees persistent sync failures in the
                # findings UI. Dedup_key keeps repeat-failures from
                # spamming. The publish_status table is the canonical
                # source of truth; this finding is the operator alert
                # surface.
                from utils.findings import emit_finding
                emit_finding(
                    source="publish_service.sync_to_cloud",
                    kind="cloud_sync_returned_false",
                    severity="warning",
                    title=f"Cloud DB sync returned False for post {post_id}",
                    body=(
                        f"SyncService.push_post({post_id}) returned False "
                        "without raising. The post is published locally "
                        "but may not be visible to the cloud read path. "
                        "Check sync_service logs + the cloud DB "
                        "connectivity."
                    ),
                    dedup_key="publish_sync_to_cloud_false",
                )
    except Exception as e:
        logger.warning("[SYNC] Failed to sync published post (non-fatal): %s", e)
        try:
            from utils.findings import emit_finding
            emit_finding(
                source="publish_service.sync_to_cloud",
                kind="cloud_sync_exception",
                severity="warning",
                title=f"Cloud DB sync raised {type(e).__name__} for post {post_id}",
                body=(
                    f"Post {post_id} published locally but cloud sync "
                    f"failed: {type(e).__name__}: {e}. Recurring "
                    "failures here mean the cloud read path is stale; "
                    "investigate sync_service network + auth."
                ),
                dedup_key=f"publish_sync_exception_{type(e).__name__}",
            )
        except Exception:
            # Never let the finding-emit path itself escalate — the
            # warning log above is already the minimum signal.
            pass


async def _ping_search_engines(
    site_url: str,
    post_url: str,
    *,
    site_config: SiteConfig,
) -> None:
    """Notify search engines about new content via IndexNow and Google ping.

    #272 Phase-2g: ``site_config`` is REQUIRED (keyword-only). The only
    callers are ``publish_post_from_task`` / ``fire_post_distribution_hooks``
    in this module, which thread their own required ``_sc``.
    """
    import httpx

    _sc = site_config

    # Tight caps on external SEO pings — they're fire-and-forget, we don't
    # want them to delay anything else if the target is slow.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0)
    ) as client:
        # IndexNow (Bing, Yandex, Naver, Seznam).
        # #198: both endpoint + key settings-backed. Setting the endpoint
        # to '' disables the ping without code changes.
        # indexnow_key is is_secret=true since 2026-05-12 — must fetch via
        # async get_secret. The endpoint URL is NOT a secret so it stays
        # on the sync cache.
        _indexnow_key = await _sc.get_secret("indexnow_key", "")
        _indexnow_url = _sc.get(
            "indexnow_ping_url", "https://api.indexnow.org/indexnow"
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
                logger.warning("[SEO] IndexNow ping failed (non-fatal): %s", e)

        # Search-engine sitemap ping (Google's /ping endpoint by default;
        # set google_sitemap_ping_url='' to skip).
        _sitemap_ping = _sc.get(
            "google_sitemap_ping_url", "https://www.google.com/ping"
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
                logger.warning("[SEO] Sitemap ping failed (non-fatal): %s", e)


async def _embed_published_post(db_service, post_dict: dict) -> None:
    """Embed a newly published post into pgvector (non-blocking)."""
    try:
        from plugins.registry import get_all_llm_providers
        from services.embedding_service import EmbeddingService

        embeddings_db = getattr(db_service, "embeddings", None)
        if not embeddings_db:
            logger.debug("[RAG] Skipping post embedding: embeddings DB not available")
            return

        # v2.2b: embed through the Provider Protocol — config-swappable
        # via plugin.llm_provider.primary.free in app_settings.
        providers = {p.name: p for p in get_all_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.debug("[RAG] Skipping post embedding: ollama_native provider not registered")
            return

        embedding_svc = EmbeddingService(provider=provider, embeddings_db=embeddings_db)
        await embedding_svc.embed_post(post_dict)
        logger.info("[RAG] Embedded published post for future RAG: %s", post_dict.get("title", "")[:60])
    except Exception as e:
        logger.warning("[RAG] Failed to embed published post (non-fatal): %s", e)
        # 2026-05-12 fail-loud sweep: RAG embedding silently failing
        # degrades the writer's search quality over time and is
        # invisible to operators. Emit a finding so persistent
        # failures show up in the findings UI.
        try:
            from utils.findings import emit_finding
            post_id_for_log = (
                post_dict.get("id") or post_dict.get("post_id") or "?"
            )
            emit_finding(
                source="publish_service.embed_published_post",
                kind="rag_embed_failed",
                severity="warning",
                title=f"RAG embedding failed for post {post_id_for_log}",
                body=(
                    f"Post {post_id_for_log} published but its embedding "
                    f"failed: {type(e).__name__}: {e}. RAG retrieval "
                    "for future posts won't include this content until "
                    "the embedding succeeds. Common causes: ollama "
                    "unreachable, embedding model unloaded, "
                    "embeddings_db disk full."
                ),
                dedup_key=f"rag_embed_failed_{type(e).__name__}",
            )
        except Exception:
            # poindexter#455 — same pattern as the devto_service /
            # category_resolver fixes earlier in the sweep. emit_finding
            # itself can fail (DB pool exhausted, audit table missing,
            # etc.); never let an observability failure poison the
            # publish path. Log at debug so it's still inspectable in
            # trace logs.
            logger.debug(
                "[publish_service] emit_finding for rag_embed_failed raised — "
                "skipping observability write",
                exc_info=True,
            )


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
    publisher: str = "operator",
    trigger_revalidation: bool = True,
    queue_social: bool = True,
    draft_mode: bool = False,
    stage_only: bool = False,
    honor_pacing: bool = False,
    background_tasks=None,
    site_config: SiteConfig,
) -> PublishResult:
    """Create a published post from a completed content task.

    This is the ONE place where a task becomes a post. All code paths
    (approve auto-publish, explicit /publish, worker auto-publish,
    approve-stages-for-schedule) call this.

    Args:
        db_service: DatabaseService instance
        task: Full task dict (from db_service.get_task)
        task_id: Task UUID string
        publisher: Who triggered the publish (for audit trail)
        trigger_revalidation: Whether to trigger ISR revalidation
        queue_social: Whether to queue social media post generation
        draft_mode: Create the posts row at ``status='draft'``. Mutually
            exclusive with ``stage_only``.
        stage_only: Create the posts row at ``status='approved'`` with
            ``published_at=NULL`` — the staging area
            ``services.scheduling_service`` queries via ``schedule batch``.
            No revalidation, no social-queue, no distribution recording —
            those fire on the eventual publish via ``scheduled_publisher``.
            Per the ``feedback_approve_does_not_mean_publish`` rule,
            approving a task without ``auto_publish`` should land here.
        background_tasks: Optional FastAPI BackgroundTasks for non-blocking work

    Returns:
        PublishResult with post details or error info
    """
    if stage_only and draft_mode:
        raise ValueError(
            "publish_post_from_task: stage_only and draft_mode are "
            "mutually exclusive (status='approved' vs status='draft')."
        )
    # #272 Phase-2g: site_config is REQUIRED — thread/read via _sc below.
    _sc = site_config
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
    # Defense at the publish boundary: peel any leaked writer JSON envelope
    # (e.g. a ```json-fenced {"title": ..., "post_body": "<markdown>"} that
    # slipped past the generation-time unwrap) so the published article is
    # markdown, never a raw envelope. Mirrors the preview-render unwrap so
    # preview and publish produce identical output. No-op on clean prose.
    from services.llm_text import maybe_unwrap_json
    draft_content = maybe_unwrap_json(draft_content)
    seo_description = merged.get("seo_description", "") or task.get("seo_description", "")
    # internal tracker: strip any stray HTML (notably <img> tags) from the SEO
    # description before it ships as the post's excerpt + meta description.
    # Upstream writers occasionally leak markup that the /posts cards then
    # render as literal text.
    if seo_description:
        seo_description = re.sub(r"<[^>]+>", "", seo_description).strip()
        seo_description = re.sub(r"\s+", " ", seo_description)
    seo_keywords = merged.get("seo_keywords", [])
    featured_image_url = merged.get("featured_image_url") or task.get("featured_image_url")
    # featured_image_data — reproducibility blob (SDXL prompt / model /
    # seed / generation_seconds for the SDXL branch, basic provenance
    # for the Pexels branch). Sourced by source_featured_image.execute
    # and threaded through pipeline_versions.stage_data ->
    # task_metadata.featured_image_data so it survives the
    # finalize_task → publish hand-off. Lands on
    # posts.featured_image_data via content_db.create_post. Closes the
    # 2026-05-19 jank-audit dead-seam finding for the column.
    featured_image_data = (
        merged.get("featured_image_data")
        or task.get("featured_image_data")
        or {}
    )
    if not isinstance(featured_image_data, dict):
        # Defensive: stage_data round-trips through JSONB so the value
        # should always be a dict on arrival, but we shouldn't crash
        # the publisher if some legacy upstream wrote a string.
        featured_image_data = {}
    metadata = merged.get("metadata", {})
    if not isinstance(metadata, dict):
        # Defensive — same shape as featured_image_data above. The
        # downstream INSERT requires a dict so it can be json.dumps'd.
        metadata = {}
    # Stamp the source task_id onto posts.metadata so the
    # posts ↔ pipeline_tasks link is a first-class structured seam
    # rather than slug-suffix archaeology. scheduled_publisher reads
    # this key to keep ``pipeline_tasks.status`` in lockstep with
    # ``posts.status`` promotions; the /go-live admin route does the
    # same. Per ``feedback_filter_on_seams_not_slugs`` — when a
    # structured field exists, populate + filter on it. The 2026-05-28
    # backfill migration populates this key for every existing post.
    if task_id:
        metadata["pipeline_task_id"] = str(task_id)

    if not draft_content or not topic:
        msg = "Missing content or topic — cannot create post"
        logger.warning("[publish_service] %s for task %s", msg, task_id)
        return PublishResult(success=False, error=msg)

    # ---------------------------------------------------------------
    # 1b. Idempotency / approve-then-publish promotion guard.
    #
    # Slugs contain task_id[:8] suffix, so we can match on that.
    #
    # 2026-05-27 fix: the guard used to return "skipping duplicate"
    # whenever any post existed — but the operator flow is two-step:
    #   1. ``approve`` calls this with stage_only=True → row at
    #      status='approved' (published_at NULL, distributed_at NULL).
    #   2. ``publish`` calls this with stage_only=False → expected to
    #      promote the existing approved row to status='published'.
    #
    # The old guard caught the publish call and silently returned
    # success without touching the row. Result: operator's
    # ``poindexter tasks publish <id>`` looked successful (HTTP 200)
    # but the post stayed at status='approved' forever, invisible to
    # the site. Surfaced by Matt 2026-05-27 on task 677cc2df.
    #
    # Behavior matrix now:
    # - existing.status='published' → idempotent no-op (real duplicate)
    # - existing.status='approved' AND stage_only=True → no-op (re-stage)
    # - existing.status='approved' AND stage_only=False → PROMOTE in
    #   place to 'published' + set published_at + distributed_at, return
    #   the existing post (no duplicate row created)
    # - existing.status='draft' → no-op (mid-edit, leave alone)
    # ---------------------------------------------------------------
    _task_suffix = task_id[:8]
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool
    existing = await pool.fetchrow(
        "SELECT id, slug, title, status FROM posts WHERE slug LIKE '%' || $1",
        _task_suffix,
    )
    if existing:
        existing_status = (existing.get("status") or "").lower()
        if existing_status == "approved" and not stage_only and not draft_mode:
            now_ts = datetime.now(timezone.utc)
            async with pool.acquire() as _promote_conn:
                async with _promote_conn.transaction():
                    await _promote_conn.execute(
                        "UPDATE posts SET status = 'published', "
                        "published_at = COALESCE(published_at, $2), "
                        "distributed_at = COALESCE(distributed_at, $2), "
                        "updated_at = $2 "
                        "WHERE id = $1",
                        existing["id"], now_ts,
                    )
                    # Sync pipeline_tasks.status in lockstep — same
                    # rationale as the scheduled_publisher loop. Reads
                    # the seam (posts.metadata->>'pipeline_task_id')
                    # populated either at insert by this same function
                    # or by the 2026-05-28 backfill migration.
                    sync_result = await _promote_conn.execute(
                        """
                        UPDATE pipeline_tasks
                           SET status = 'published',
                               updated_at = NOW()
                         WHERE task_id = $1
                           AND status IN ('approved', 'scheduled')
                        """,
                        str(task_id),
                    )
                    logger.info(
                        "[publish_service] Promote-path pipeline_tasks "
                        "sync for task=%s: %s",
                        task_id, sync_result,
                    )
            logger.info(
                "[publish_service] Promoted existing approved post to "
                "published: task=%s post_id=%s slug=%s",
                task_id, existing["id"], existing["slug"],
            )
            # Push to R2 inline. Skipping this is what caused the
            # 2026-05-27 R2 drift alert — the short-circuit returned
            # before reaching the main path's ``export_post`` call,
            # leaving R2 one post stale until the
            # static_export_reconciliation probe caught up. Mirror the
            # same try/except shape as the main path (lines ~1090).
            promote_export_success = False
            try:
                from services.static_export_service import export_post

                promote_export_success = await export_post(
                    pool, existing["slug"], site_config=_sc,
                )
                if not promote_export_success:
                    logger.warning(
                        "[STATIC_EXPORT] Promote-path export_post returned "
                        "failure for %s — R2 stays stale until next "
                        "reconciliation cycle",
                        existing["slug"],
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[STATIC_EXPORT] Promote-path export_post raised for "
                    "%s: %s",
                    existing["slug"], exc, exc_info=True,
                )
            return PublishResult(
                success=True,
                post_id=str(existing["id"]),
                post_slug=existing["slug"],
                published_url=f"/posts/{existing['slug']}",
                post_title=existing.get("title", topic),
                static_export_success=promote_export_success,
            )
        logger.warning(
            "[publish_service] Post already exists for task %s "
            "(post_id=%s, slug=%s, status=%s) — skipping duplicate",
            task_id, existing["id"], existing["slug"], existing_status,
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
    # 4c. Resolve tags (internal tracker) — derive post.tag_ids from the task's
    # submitted tags + seo_keywords, upsert into the `tags` table so new
    # terms auto-create, and pass to content_db.create_post which will
    # populate post_tags junction. Empty tags → no-op (downstream code
    # tolerates missing tag_ids).
    # ---------------------------------------------------------------
    candidate_tag_strings: list[str] = []
    # Task-level tags (now threaded via ModelConverter.to_task_response
    # since internal tracker fix — pulls from metadata JSONB as fallback).
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
    # Strict-mode gate enforcement (#24): if this post will have
    # approval gates, it must NOT land at status='published' on initial
    # insert — that would make the URL live + index it on the static
    # export before the operator approves. Instead we land at
    # 'awaiting_gates'. fire_post_distribution_hooks (called after the
    # last gate clears) flips it to 'published'.
    #
    # The gate ROWS may not exist yet at this exact moment (some flows
    # create them just after the post insert), so we use the planned
    # gate list passed in via task['gates'] OR check existing rows. If
    # neither is present, we treat it as a no-gate autonomous publish.
    _planned_gates = task.get("gates") or []
    _strict_mode_status = (
        "awaiting_gates" if (_planned_gates and not draft_mode) else "published"
    )
    # stage_only overrides the normal status decision — see the function
    # docstring. The status flips to 'approved' so services.scheduling_service
    # (schedule batch CLI) treats it as a staged candidate; published_at
    # stays NULL so it doesn't go live until something promotes it.
    if stage_only:
        _strict_mode_status = "approved"
    # Resolve media_to_generate from the niche's configured policy.
    # ``niches.default_media_to_generate`` is the canonical seam (added
    # by migration 20260519_134736). Falls back to an empty array when
    # the niche row is missing or has no policy set — safer to skip
    # media generation than to silently default to "spawn everything"
    # for an unknown niche. Closes Glad-Labs/glad-labs-stack#480 and
    # the post-mortem from #481 (slug-pattern filter Matt rejected
    # 2026-05-19).
    niche_slug = task.get("niche_slug") or ""
    media_to_generate: list[str] = []
    if niche_slug:
        try:
            pool = getattr(db_service, "pool", None)
            if pool is not None:
                async with pool.acquire() as _conn:
                    row = await _conn.fetchrow(
                        "SELECT default_media_to_generate "
                        "FROM niches WHERE slug = $1",
                        niche_slug,
                    )
                    if row and row["default_media_to_generate"]:
                        media_to_generate = list(row["default_media_to_generate"])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[publish_service] niche media policy lookup failed for "
                "niche=%r (defaulting to empty): %s",
                niche_slug, exc,
            )

    # Glad-Labs/glad-labs-stack#649 PR 2 — propagate the director's
    # shot list from task_metadata to posts.video_shot_list so the
    # shot-list renderer can find it when ``generate_video_for_post``
    # runs for this post. Absent when the director stage skipped (no
    # pool / no podcast script / etc.) — the column lands NULL and the
    # renderer falls back to the legacy slideshow path.
    video_shot_list = merged.get("video_shot_list")

    post_data: dict[str, Any] = {
        "title": post_title,
        "slug": slug,
        "content": post_content,
        "excerpt": seo_description,
        "featured_image_url": featured_image_url,
        "cover_image_url": featured_image_url,
        "author_id": author_id,
        "category_id": category_id,
        "status": "draft" if draft_mode else _strict_mode_status,
        "seo_title": post_title,
        "seo_description": seo_description,
        "seo_keywords": ", ".join(seo_keywords) if isinstance(seo_keywords, list) else (seo_keywords or ""),
        "metadata": metadata,
        "tag_ids": tag_ids or None,
        "media_to_generate": media_to_generate,
        "featured_image_data": featured_image_data,
        "video_shot_list": video_shot_list,
    }
    if scheduled_at:
        post_data["published_at"] = scheduled_at

    # Mark posts as eligible for feed distribution only when they will
    # be visible immediately. Staged posts (status='approved') and drafts
    # stay out of the feed until they're scheduled+published.
    if not draft_mode and not stage_only:
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

    # Back-stamp media_assets rows that were recorded by upstream stages
    # (e.g. source_featured_image) before this post existed. Those rows
    # are persisted with ``post_id=NULL`` but carry the producing
    # ``task_id`` — closing the FK here turns them into proper child
    # rows of the post. Glad-Labs/glad-labs-stack#193.
    try:
        pool = getattr(db_service, "pool", None)
        if pool is not None and task_id and post_id:
            async with pool.acquire() as _conn:
                result = await _conn.execute(
                    """
                    UPDATE media_assets
                       SET post_id = $1
                     WHERE task_id = $2
                       AND post_id IS NULL
                    """,
                    post_id,
                    task_id,
                )
                logger.info(
                    "[publish_service] media_assets back-stamp: task_id=%s post_id=%s %s",
                    task_id, post_id, result,
                )
    except Exception as exc:  # noqa: BLE001 — back-stamp is best-effort
        logger.warning(
            "[publish_service] media_assets back-stamp failed for task_id=%s post_id=%s: %s",
            task_id, post_id, exc,
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

    # stage_only short-circuit: the posts row exists at status='approved'
    # with published_at=NULL, the pipeline_task already sits at 'approved'
    # from the approve_task handler that called us. We leave it there
    # (do NOT flip to 'published'), record the post_id back on the task
    # for later traceability, then return. Skipping the post-publish
    # webhook/cloud-sync/distribution side-effects entirely — those
    # fire when scheduled_publisher promotes the staged row to
    # 'published'. See `feedback_approve_does_not_mean_publish`.
    if stage_only:
        try:
            await db_service.update_task_status(
                task_id, "approved", result=safe_json_dumps(convert_decimals(merged))
            )
        except Exception as e:
            logger.warning(
                "[publish_service] stage_only: failed to backstamp post_id "
                "onto task %s result: %s", task_id, e,
            )
        logger.info(
            "[publish_service] stage_only post created at status='approved' "
            "(task_id=%s post_id=%s slug=%s) — eligible for schedule batch",
            task_id, post_id, slug,
        )
        return PublishResult(
            success=True,
            post_id=post_id,
            post_slug=slug,
            published_url=f"/posts/{slug}",
            staged=True,
        )

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
    # 8b. Gate engine — defer distribution if any approval gate is pending
    # ---------------------------------------------------------------
    # Glad-Labs/poindexter#24: the per-medium gate machinery can pause
    # the workflow at a `final` (or earlier) checkpoint between content
    # creation and distribution. When any gate row for this post is
    # still ``pending``, skip the social/devto/podcast/video/short/RSS
    # hooks and let ``fire_post_distribution_hooks`` re-run them once
    # the gate is approved.
    #
    # Posts that pre-date the gate engine (or were created without any
    # ``--gates``) have zero pending rows, so this is a no-op for the
    # autonomous path (back-compat preserved).
    _gate_pool = getattr(db_service, "cloud_pool", None) or db_service.pool
    _gates_block_distribution = await _post_has_pending_gates(_gate_pool, post_id)
    if _gates_block_distribution:
        logger.info(
            "[publish_service] Post %s has pending approval gates — "
            "deferring distribution hooks until gates clear (#24)",
            post_id,
        )

    # ---------------------------------------------------------------
    # 9. Queue social media post generation
    # ---------------------------------------------------------------
    if queue_social and not _gates_block_distribution:
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
            logger.warning("[SOCIAL] Social posting failed (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 9b. Queue Dev.to cross-posting (fire-and-forget)
    # ---------------------------------------------------------------
    if not _gates_block_distribution:
        try:
            from services.devto_service import DevToCrossPostService

            # Thread the lifespan-bound site_config through so the
            # DevTo crosspost can read site_url for the canonical URL.
            # Without this kwarg the service falls back to a fresh
            # empty SiteConfig and crashes on .require("site_url") —
            # observed during the 2026-05-17 auto-publish stress test.
            devto_svc = DevToCrossPostService(
                getattr(db_service, "cloud_pool", None) or db_service.pool,
                site_config=_sc,
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
            logger.warning("[DEVTO] Cross-posting setup failed (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 10. ISR revalidation
    # ---------------------------------------------------------------
    # Glad-Labs/poindexter#327: routed through trigger_isr_revalidate
    # so every publish path (canonical, /go-live, scheduled_publisher)
    # uses the same code that knows the canonical paths/tags +
    # async get_secret() flow.
    #
    # Strict-mode gate (#24): if the post has pending gates, do NOT
    # revalidate — the post status is 'awaiting_gates', so revalidation
    # would just expose a 404 to anyone who clicks. fire_post_distribution_hooks
    # fires ISR revalidate after gates clear.
    revalidation_success = False
    if trigger_revalidation and not _gates_block_distribution:
        try:
            from services.revalidation_service import trigger_isr_revalidate

            revalidation_success = await trigger_isr_revalidate(slug, site_config=_sc)
            if not revalidation_success:
                logger.warning("[publish_service] ISR revalidation returned failure for %s", slug)
        except Exception as reval_err:
            logger.warning("[publish_service] ISR revalidation error (non-fatal): %s", reval_err)

    # ---------------------------------------------------------------
    # 10b. Static JSON export to CDN (inline, not fire-and-forget)
    # ---------------------------------------------------------------
    # The public site reads R2 ``static/posts/index.json`` as its source
    # of truth (web/public-site/lib/posts.ts → ``fetchPostIndex``). When
    # this step is fire-and-forget, any process boundary that cancels
    # the asyncio task (Prefect subprocess teardown for auto-publish,
    # worker restart, transient hiccup) silently freezes R2 — between
    # 2026-05-08 and 2026-05-11 four published posts never reached the
    # bucket because the background task never completed.
    #
    # Awaiting inline costs ~3-5s on R2 (5 small JSON files) which is
    # well within the publish-endpoint latency budget. The return value
    # lands on PublishResult.static_export_success so /approve + /publish
    # callers can surface the failure to the operator.
    #
    # Strict-mode gate (#24): same reason as ISR — static export filters
    # on status='published', so 'awaiting_gates' posts are excluded
    # automatically. But we ALSO skip the export call entirely as a
    # belt-and-suspenders against future filter changes.
    static_export_success = False
    if not _gates_block_distribution:
        try:
            from services.static_export_service import export_post

            _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
            static_export_success = await export_post(_pool, slug, site_config=_sc)
            if static_export_success:
                logger.info("[STATIC_EXPORT] Synchronous export complete for %s", slug)
            else:
                logger.warning(
                    "[STATIC_EXPORT] export_post returned failure for %s — "
                    "R2 index will be stale until the reconciliation watchdog "
                    "fires or the next successful publish completes",
                    slug,
                )
        except Exception as e:
            logger.error(
                "[STATIC_EXPORT] export_post raised for %s: %s",
                slug, e, exc_info=True,
            )

    # ---------------------------------------------------------------
    # 11. Ping search engines (fire-and-forget)
    # ---------------------------------------------------------------
    site_url = _sc.require("site_url")
    published_url_full = f"{site_url}/posts/{slug}"
    if not _gates_block_distribution:
        _spawn_background(
            _ping_search_engines(site_url, published_url_full, site_config=_sc),
            name=f"ping_search_engines({slug})",
        )

    # ---------------------------------------------------------------
    # 11b/c/d. Generate derived media (podcast / video / short).
    # ---------------------------------------------------------------
    # Per-type gating on ``media_to_generate`` so a post whose niche
    # policy excludes a media type (e.g. dev_diary with ``[]``) doesn't
    # silently spawn unwanted media on initial publish. The gate-clearing
    # path (``fire_post_distribution_hooks`` below) already gates on this
    # array; the initial publish path was missing the same check —
    # captured 2026-05-20 (finding #196) on post ``dcd86ea6...`` whose
    # ``media_to_generate=[]`` still triggered podcast + video generation
    # at publish time. Matches the spawn-conditions in
    # ``fire_post_distribution_hooks`` so the two paths stay aligned.
    _pre_script = merged.get("podcast_script") or ""
    _video_scenes = merged.get("video_scenes") or []
    _short_summary = merged.get("short_summary_script") or ""
    _wants_podcast = "podcast" in (media_to_generate or [])
    _wants_video = any(
        v in (media_to_generate or [])
        for v in ("video", "video_long")
    )
    _wants_short = "video_short" in (media_to_generate or [])

    # 11b. Podcast episode.
    if (
        _should_run_post_publish_hooks()
        and not _gates_block_distribution
        and _wants_podcast
    ):
        try:
            from services.podcast_service import generate_podcast_episode

            # #539 — comma-join the SEO keyword list once for the media
            # hooks so the podcast / video media_assets rows carry the
            # same SEO fields the post already generated (reused, no LLM
            # regeneration). ``seo_keywords`` is a list at this point;
            # ``posts.seo_keywords`` is persisted comma-joined (line ~907).
            _seo_keywords_str = (
                ", ".join(seo_keywords)
                if isinstance(seo_keywords, list)
                else (seo_keywords or "")
            )

            async def _gen_podcast_with_gate(pid, ptitle, pcontent, script):
                """Run generation, then record the approval-gate row on success.

                Wrapper keeps ``record_pending`` from firing on the
                fire-and-forget side BEFORE the file actually exists.
                A failed generation leaves no row, which is the
                correct state — backfill_podcasts.py will retry the
                generation later and insert the row at that point.
                """
                try:
                    result = await generate_podcast_episode(
                        pid, ptitle, pcontent,
                        pre_generated_script=script,
                        site_config=_sc,
                        seo_description=seo_description,
                        seo_keywords=_seo_keywords_str,
                    )
                except Exception as gen_err:
                    logger.warning(
                        "[PODCAST] Generation failed for post %s "
                        "(no gate row written; backfill will retry): %s",
                        pid, gen_err,
                    )
                    return
                if not (result and getattr(result, "success", False)):
                    logger.warning(
                        "[PODCAST] Generation returned non-success for %s; "
                        "no gate row written", pid,
                    )
                    return
                try:
                    from services import media_approval_service
                    _pool = (
                        getattr(db_service, "cloud_pool", None)
                        or db_service.pool
                    )
                    if _pool is None:
                        logger.warning(
                            "[PODCAST] no db pool to record approval gate "
                            "for post %s — operator must insert manually",
                            pid,
                        )
                        return
                    async with _pool.acquire() as _conn:
                        await media_approval_service.record_pending(
                            _conn, pid, "podcast",
                        )
                except Exception as gate_err:
                    logger.warning(
                        "[PODCAST] gate insert failed for %s "
                        "(file exists, no gate row): %s",
                        pid, gate_err,
                    )

            if background_tasks:
                background_tasks.add_task(
                    _gen_podcast_with_gate, post_id, post_title, post_content,
                    _pre_script,
                )
            else:
                _spawn_background(
                    _gen_podcast_with_gate(post_id, post_title, post_content,
                                          _pre_script),
                    name=f"podcast_episode({post_id})",
                )
            logger.info("[PODCAST] Queued episode generation for post %s", post_id)
        except Exception as e:
            logger.warning("[PODCAST] Failed to queue episode (non-fatal): %s", e)

    # 11c. Long-form video episode.
    if (
        _should_run_post_publish_hooks()
        and not _gates_block_distribution
        and _wants_video
    ):
        try:
            from services.video_service import generate_video_episode

            if background_tasks:
                background_tasks.add_task(
                    generate_video_episode, post_id, post_title, post_content,
                    pre_generated_scenes=_video_scenes,
                    site_config=_sc,
                    seo_description=seo_description,
                    seo_keywords=_seo_keywords_str,
                )
            else:
                _spawn_background(
                    generate_video_episode(post_id, post_title, post_content,
                                          pre_generated_scenes=_video_scenes,
                                          site_config=_sc,
                                          seo_description=seo_description,
                                          seo_keywords=_seo_keywords_str),
                    name=f"video_episode({post_id})",
                )
            logger.info("[VIDEO] Queued video generation for post %s", post_id)
        except Exception as e:
            logger.warning("[VIDEO] Failed to queue video (non-fatal): %s", e)

    # 11d. Short-form video.
    if (
        _should_run_post_publish_hooks()
        and not _gates_block_distribution
        and _wants_short
    ):
        try:
            from services.video_service import generate_short_video_for_post

            async def _gen_short(pid, ptitle, pcontent, scenes, short_script):
                """Wait for podcast, then generate short video."""
                import asyncio as _aio

                _delay = int(_sc.get("short_video_post_publish_delay_seconds", "180"))
                await _aio.sleep(_delay)
                try:
                    result = await generate_short_video_for_post(
                        pid, ptitle, pcontent,
                        pre_generated_scenes=scenes,
                        pre_generated_summary=short_script,
                        site_config=_sc,
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
            logger.warning("[SHORT] Failed to queue short video (non-fatal): %s", e)

    # ---------------------------------------------------------------
    # 11e. Upload media to R2 CDN (fire-and-forget, after generation)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks() and not _gates_block_distribution:
        async def _upload_media_to_r2(pid: str) -> None:
            """Wait for media files to appear, then upload to R2."""
            import asyncio as _aio
            from pathlib import Path

            from services.r2_upload_service import R2UploadService
            _r2 = R2UploadService(site_config=_sc)
            # Give podcast/video/short generation time to complete
            _delay = int(_sc.get("media_upload_delay_seconds", "240"))
            await _aio.sleep(_delay)
            await _r2.upload_podcast_episode(pid)
            await _r2.upload_video_episode(pid)
            # Upload short video if it exists
            short_path = Path(os.path.expanduser("~")) / ".poindexter" / "video" / f"{pid}-short.mp4"
            if short_path.exists():
                await _r2.upload_to_r2(str(short_path), f"video/{pid}-short.mp4", "video/mp4")
            # Regenerate public podcast RSS feed on R2
            try:
                import httpx as _hx

                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                _api_base = _sc.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
                # Per-call temp file via tempfile.mkstemp avoids hardcoded
                # /tmp paths (Bandit B108) and prevents collisions when
                # multiple publishes run concurrently.
                _fd, _feed_path = tempfile.mkstemp(suffix=".xml", prefix="poindexter-podcast-")
                try:
                    os.close(_fd)  # _write_text_file reopens the path
                    async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=5.0)) as _client:
                        _feed = await _client.get(f"{_api_base}/api/podcast/feed.xml", timeout=30)
                        # Blocking file I/O in async context — push to worker thread
                        # so the event loop isn't stalled while we write the feed file.
                        await asyncio.to_thread(
                            _write_text_file, _feed_path, _feed.text,
                        )
                        await _r2.upload_to_r2(_feed_path, "podcast/feed.xml", "application/rss+xml")
                        logger.info("[R2] Podcast RSS feed regenerated on CDN")
                finally:
                    try:
                        os.unlink(_feed_path)
                    except OSError:
                        pass
            except Exception as _e:
                logger.warning("[R2] Podcast feed regen failed (non-fatal): %s", _e)

            # Regenerate public video RSS feed on R2
            try:
                import httpx as _hx

                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                _api_base = _sc.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
                # Per-call temp file via tempfile.mkstemp avoids hardcoded
                # /tmp paths (Bandit B108).
                _fd, _feed_path = tempfile.mkstemp(suffix=".xml", prefix="poindexter-video-")
                try:
                    os.close(_fd)
                    async with _hx.AsyncClient(timeout=_hx.Timeout(30.0, connect=5.0)) as _client:
                        _feed = await _client.get(f"{_api_base}/api/video/feed.xml", timeout=30)
                        await asyncio.to_thread(
                            _write_text_file, _feed_path, _feed.text,
                        )
                        await _r2.upload_to_r2(_feed_path, "video/feed.xml", "application/rss+xml")
                        logger.info("[R2] Video RSS feed regenerated on CDN")
                finally:
                    try:
                        os.unlink(_feed_path)
                    except OSError:
                        pass
            except Exception as _e:
                logger.warning("[R2] Video feed regen failed (non-fatal): %s", _e)

            # YouTube upload removed in the 2026-05-08 services audit cleanup —
            # the stub adapter raised NotImplementedError and there's no real
            # implementation behind it yet. See poindexter#449 for the OAuth
            # setup checklist; re-wire this branch when the real adapter ships.

        _spawn_background(
            _upload_media_to_r2(post_id), name=f"upload_media_r2({post_id})"
        )

    # ---------------------------------------------------------------
    # 11f. Newsletter to subscribers (fire-and-forget)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks() and not _gates_block_distribution:
        async def _send_newsletter(_pid: str, ptitle: str, pexcerpt: str, pslug: str) -> None:
            try:
                from services.newsletter_service import send_post_newsletter
                _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
                # #272 Phase-2b/2g: newsletter_service requires a site_config —
                # pass the run-bound instance resolved at the top of
                # ``publish_post_from_task`` (the ``_sc`` closure variable).
                # (Pre-2g this read the outer ``site_config`` param, which was
                # the as-passed kwarg — possibly None — a latent bug now that
                # injection is mandatory.)
                result = await send_post_newsletter(
                    _pool, ptitle, pexcerpt, pslug, site_config=_sc,
                )
                logger.info("[NEWSLETTER] Result: %s", result)
            except Exception as e:
                logger.warning("[NEWSLETTER] Failed (non-fatal): %s", e)

        _spawn_background(
            _send_newsletter(post_id, post_title, seo_description, slug),
            name=f"send_newsletter({post_id})",
        )

    # ---------------------------------------------------------------
    # 12. Send notification
    # ---------------------------------------------------------------
    try:
        from services.integrations.operator_notify import notify_operator

        _q_score = task.get("quality_score") or merged.get("quality_score") or "N/A"
        await notify_operator(
            f"Published: {post_title}\n/posts/{slug}\nScore: {_q_score}",
            critical=True,
        )
    except Exception:
        logger.warning("[publish_service] Notification failed (non-fatal)", exc_info=True)

    # ---------------------------------------------------------------
    # 13. Edit-distance metrics — auto_publish_gate training signal.
    #     Pre-approve content is what finalize_task wrote into
    #     task_metadata.content_text; post-approve is what actually
    #     shipped. Diff is the operator's edit distance — the gate's
    #     primary trust signal per
    #     feedback_auto_publish_requires_edit_distance_track_record.
    # ---------------------------------------------------------------
    try:
        from services.auto_publish_gate import record_post_approve_metrics
        # Pre-approve snapshot lives in task_metadata under "pre_approve_content"
        # (written by finalize_task). Falls back to "content" — same key
        # publish_service reads as the post-approve content. Falling back to
        # post_approve when the snapshot is missing produces a 0-char-diff
        # row (operator made no edits or snapshot wasn't captured), which
        # is a more accurate signal than a fabricated full-content delta.
        pre_approve = (
            task_metadata.get("pre_approve_content")
            or merged.get("pre_approve_content")
            or task_metadata.get("content")
            or merged.get("content")
            or ""
        )
        post_approve = draft_content or ""
        pool = getattr(db_service, "pool", None)
        await record_post_approve_metrics(
            pool,
            task_id=str(task_id),
            pre_approve_content=pre_approve,
            post_approve_content=post_approve,
            niche_slug=task.get("niche_slug") or merged.get("niche_slug"),
            category=task.get("category") or merged.get("category"),
            approver=publisher,
            approve_method="publish_post_from_task",
            post_id=int(post_id) if str(post_id).isdigit() else None,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "[publish_service] edit-distance metrics failed (non-fatal)",
            exc_info=True,
        )

    return PublishResult(
        success=True,
        post_id=post_id,
        post_slug=slug,
        published_url=f"/posts/{slug}",
        post_title=post_title,
        revalidation_success=revalidation_success,
        static_export_success=static_export_success,
    )


# ---------------------------------------------------------------------------
# Gate-engine re-trigger (Glad-Labs/poindexter#24)
# ---------------------------------------------------------------------------


async def fire_post_distribution_hooks(
    db_service,
    post_id: str,
    *,
    site_config: SiteConfig,
) -> dict[str, Any]:
    """Re-fire the distribution hooks (social/devto/podcast/video/short/RSS)
    for a post whose approval gates have just cleared.

    Reads the post + the writer task it came from, then fans out the
    same hooks ``publish_post_from_task`` would have fired immediately
    for an autonomous post.

    Returns a small descriptor of what was triggered. All errors are
    swallowed and logged — distribution hooks are best-effort by
    design (the post is already on the public site at this point).
    """
    # #272 Phase-2g: site_config is REQUIRED — read/thread via _sc below.
    _sc = site_config
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool

    # Defensive: don't fire if there are still pending gates. Concurrent
    # operators or callers re-triggering speculatively shouldn't be
    # able to bypass a subsequent ``revise``.
    if await _post_has_pending_gates(pool, post_id):
        logger.info(
            "[publish_service] fire_post_distribution_hooks: post %s "
            "still has pending gates — refusing to fire distribution",
            post_id,
        )
        return {"fired": False, "reason": "pending_gates"}

    async with pool.acquire() as conn:
        post_row = await conn.fetchrow(
            """
            SELECT id::text AS id, title, slug, content, excerpt,
                   seo_keywords, media_to_generate
              FROM posts
             WHERE id::text = $1
            """,
            str(post_id),
        )
    if post_row is None:
        logger.warning(
            "[publish_service] fire_post_distribution_hooks: post %s "
            "not found",
            post_id,
        )
        return {"fired": False, "reason": "post_not_found"}

    post_title = post_row["title"]
    slug = post_row["slug"]
    post_content = post_row["content"] or ""
    seo_description = post_row["excerpt"] or ""
    seo_keywords_str = post_row["seo_keywords"] or ""
    seo_keywords = [k.strip() for k in seo_keywords_str.split(",") if k.strip()]
    media = list(post_row["media_to_generate"] or [])

    fired: dict[str, Any] = {"fired": True, "post_id": post_id, "hooks": []}

    # ---------------------------------------------------------------
    # Strict-mode gate enforcement (#24): the post was inserted at
    # status='awaiting_gates' to keep it off the public site while
    # gates were pending. Now that all gates have cleared, flip it to
    # 'published' so the static export query (status='published')
    # picks it up + the page route resolves. Then fire ISR revalidate
    # + static export so the public surfaces refresh immediately.
    # ---------------------------------------------------------------
    async with pool.acquire() as conn:
        async with conn.transaction():
            update_result = await conn.execute(
                """
                UPDATE posts
                   SET status = 'published',
                       published_at = COALESCE(published_at, NOW()),
                       updated_at = NOW()
                 WHERE id::text = $1 AND status = 'awaiting_gates'
                """,
                str(post_id),
            )
            # Sync the linked pipeline_tasks row in the same
            # transaction. The seam (metadata->>'pipeline_task_id')
            # was either stamped at insert by publish_post_from_task
            # or backfilled by migration 20260528_021920. NULL is
            # tolerated (the UPDATE simply matches no rows).
            sync_result = await conn.execute(
                """
                UPDATE pipeline_tasks pt
                   SET status = 'published',
                       updated_at = NOW()
                  FROM posts p
                 WHERE p.id::text = $1
                   AND p.metadata ->> 'pipeline_task_id' = pt.task_id
                   AND pt.status IN ('approved', 'scheduled')
                """,
                str(post_id),
            )
            logger.info(
                "[publish_service] fire_post_distribution_hooks "
                "pipeline_tasks sync for post=%s: %s",
                post_id, sync_result,
            )
    if update_result.startswith("UPDATE 1"):
        logger.info(
            "[publish_service] fire_post_distribution_hooks: flipped post %s "
            "from awaiting_gates → published",
            post_id,
        )
        fired["status_flipped"] = True

        # Static export (R2) so the post becomes fetchable by the
        # public-site getPostBySlug call.
        try:
            from services.static_export_service import export_post
            _spawn_background(
                export_post(pool, slug, site_config=_sc),
                name=f"static_export({slug})",
            )
            fired["hooks"].append("static_export")
        except Exception as e:
            logger.warning(
                "[publish_service] static_export on gate-clear failed "
                "(non-fatal): %s", e,
            )

        # ISR revalidate so Vercel rebuilds the slug page immediately
        # rather than waiting for natural ISR expiry.
        try:
            from services.revalidation_service import trigger_isr_revalidate
            ok = await trigger_isr_revalidate(slug, site_config=_sc)
            if ok:
                fired["hooks"].append("isr_revalidate")
        except Exception as e:
            logger.warning(
                "[publish_service] ISR revalidate on gate-clear failed "
                "(non-fatal): %s", e,
            )

    # 1. Social media
    try:
        from services.social_poster import generate_and_distribute_social_posts
        _spawn_background(
            generate_and_distribute_social_posts(
                title=post_title, slug=slug,
                excerpt=seo_description, keywords=seo_keywords,
                site_config=_sc,
            ),
            name=f"social_posts({slug})",
        )
        fired["hooks"].append("social")
    except Exception as e:
        logger.warning("[SOCIAL] Failed in re-trigger (non-fatal): %s", e)

    # 2. Dev.to
    try:
        from services.devto_service import DevToCrossPostService
        devto_svc = DevToCrossPostService(pool, site_config=_sc)
        _spawn_background(
            devto_svc.cross_post_by_post_id(post_id),
            name=f"devto_crosspost({post_id})",
        )
        fired["hooks"].append("devto")
    except Exception as e:
        logger.warning("[DEVTO] Failed in re-trigger (non-fatal): %s", e)

    # 3. Search engine pings
    try:
        site_url = _sc.require("site_url")
        _spawn_background(
            _ping_search_engines(site_url, f"{site_url}/posts/{slug}", site_config=_sc),
            name=f"ping_search_engines({slug})",
        )
        fired["hooks"].append("search_engines")
    except Exception as e:
        logger.warning("[SEO] Failed in re-trigger (non-fatal): %s", e)

    # 4. Per-medium generation — only fire for media in media_to_generate.
    # #539 — comma-join the SEO keyword list once so the podcast / video
    # media_assets rows carry the same SEO fields the post already
    # generated (reused from the posts row, no LLM regeneration).
    _seo_keywords_str = (
        ", ".join(seo_keywords)
        if isinstance(seo_keywords, list)
        else (seo_keywords or "")
    )
    if _should_run_post_publish_hooks():
        if "podcast" in media:
            try:
                from services.podcast_service import generate_podcast_episode
                _spawn_background(
                    generate_podcast_episode(
                        post_id, post_title, post_content, site_config=_sc,
                        seo_description=seo_description,
                        seo_keywords=_seo_keywords_str,
                    ),
                    name=f"podcast_episode({post_id})",
                )
                fired["hooks"].append("podcast")
            except Exception as e:
                logger.warning("[PODCAST] Failed in re-trigger (non-fatal): %s", e)
        if "video" in media:
            try:
                from services.video_service import generate_video_episode
                _spawn_background(
                    generate_video_episode(post_id, post_title, post_content,
                                          site_config=_sc,
                                          seo_description=seo_description,
                                          seo_keywords=_seo_keywords_str),
                    name=f"video_episode({post_id})",
                )
                fired["hooks"].append("video")
            except Exception as e:
                logger.warning("[VIDEO] Failed in re-trigger (non-fatal): %s", e)
        if "short" in media:
            try:
                from services.video_service import generate_short_video_for_post
                _spawn_background(
                    generate_short_video_for_post(post_id, post_title, post_content,
                                                 site_config=_sc),
                    name=f"short_video({post_id})",
                )
                fired["hooks"].append("short")
            except Exception as e:
                logger.warning("[SHORT] Failed in re-trigger (non-fatal): %s", e)

    logger.info(
        "[publish_service] Re-fired %d distribution hook(s) for post %s: %s",
        len(fired["hooks"]), post_id, fired["hooks"],
    )
    return fired
