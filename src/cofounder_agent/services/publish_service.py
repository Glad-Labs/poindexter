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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services.logger_config import get_logger
from services.media_policy import resolve_media_to_generate
from services.site_config import SiteConfig
from utils.text_utils import extract_title_from_content, strip_title_label

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


# Test-harness topic suffixes (``(2026-05-11 17:48 batch C #5)``,
# ``(... overnight B #1)``) leaked into live ``posts.title`` — audit
# 2026-06-02 found 2 such 112-117 char titles on gladlabs.io (issue #4).
# Strip them at publish time so a harness suffix can never reach a live
# title (or the slug derived from it) again. Conservative: only a trailing
# parenthetical that starts with a date OR contains a ``batch <letter>`` /
# ``overnight`` marker is stripped — legit parentheticals like
# "(A Practical Guide)" are preserved.
_TITLE_SUFFIX_RE = re.compile(
    r"\s*\((?:\d{4}-\d{2}-\d{2}[^)]*"
    r"|[^)]*\bbatch\s+[A-Za-z]\b[^)]*"
    r"|[^)]*\bovernight\b[^)]*)\)\s*$",
    re.IGNORECASE,
)


def sanitize_published_title(title: str | None) -> str:
    """Strip test-harness batch/debug suffixes from a title before publish.

    See ``_TITLE_SUFFIX_RE``. Returns ``""`` for ``None``/empty input.
    """
    if not title:
        return title or ""
    cleaned = _TITLE_SUFFIX_RE.sub("", title).strip()
    # #728: also strip a leaked ``Title:``/``Headline:`` label so it can
    # never reach a live title or the slug derived from it.
    return strip_title_label(cleaned)


def build_post_slug(title: str, task_id: str) -> str:
    """Derive a URL slug from *title* + *task_id*.

    Collapses runs of hyphens to a single hyphen (#728) so titles with
    an em-dash or a double-hyphen (e.g. ``"windows -- why"`` or
    ``"windows -- why"``) don't yield ``"windows----why"`` slugs. The
    transform is generator-only -- existing slugs are untouched.
    """
    base = re.sub(r"[^\w\s-]", "", title or "").lower().replace(" ", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")[:50].rstrip("-")
    return f"{base or 'post'}-{task_id[:8]}"


def choose_excerpt(
    *,
    task_metadata: dict | None,
    merged: dict | None,
    seo_description: str | None,
    title: str | None,
) -> str:
    """Pick the best excerpt for a post, never echoing the title (#728).

    Prefers the pipeline-computed excerpt (``task_metadata``/``merged``),
    falling back to the SEO description (the prior behaviour). Returns
    ``""`` when the only candidate is a verbatim copy of the title -- an
    empty excerpt is honest, a title-as-excerpt is not.
    """
    candidate = (
        (task_metadata or {}).get("excerpt")
        or (merged or {}).get("excerpt")
        or seo_description
        or ""
    ).strip()
    if candidate.casefold() == (title or "").strip().casefold():
        return ""
    return candidate


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


async def _embed_published_post(db_service, post_dict: dict, site_config: "SiteConfig | None" = None) -> None:
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

        embed_model = (
            site_config.get("embedding_model", "") or "nomic-embed-text"
            if site_config is not None
            else "nomic-embed-text"
        )
        embedding_svc = EmbeddingService(provider=provider, embeddings_db=embeddings_db, embed_model=embed_model)
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


# ===========================================================================
# publish_post_from_task — decomposed phases (Glad-Labs/poindexter#623)
#
# publish_post_from_task is "the ONE place where a task becomes a post". It was
# a 941-line god-function; #623 split it into the named phases below, each
# independently testable, with the public entrypoint reduced to a readable
# orchestrator. Behavior is preserved exactly — pinned by the existing
# publish-service test suite.
# ===========================================================================


@dataclass
class _ParsedPublishInputs:
    """Parsed + normalized inputs derived from a task, ready to build a post.

    Phase 1 of :func:`publish_post_from_task` — pure (no I/O). Produced by
    :func:`_parse_publish_inputs`.
    """

    merged: dict[str, Any]
    task_metadata: dict[str, Any]
    topic: str
    draft_content: str
    seo_description: str
    seo_keywords: Any
    featured_image_url: Any
    featured_image_data: dict[str, Any]
    metadata: dict[str, Any]


def _parse_publish_inputs(
    task: dict[str, Any], task_id: str
) -> _ParsedPublishInputs | PublishResult:
    """Phase 1 — parse + normalize the task into publish-ready inputs.

    Merges ``task_metadata`` with ``result`` (result wins), pulls topic /
    content / SEO / featured-image fields with their fallback chains, peels any
    leaked writer JSON envelope off the content, strips stray HTML from the SEO
    description, and stamps ``pipeline_task_id`` onto the post metadata seam.

    Returns the parsed inputs, or a failed :class:`PublishResult` when the task
    is missing the content or topic required to create a post.
    """
    from services.llm_text import maybe_unwrap_json

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

    return _ParsedPublishInputs(
        merged=merged,
        task_metadata=task_metadata,
        topic=topic,
        draft_content=draft_content,
        seo_description=seo_description,
        seo_keywords=seo_keywords,
        featured_image_url=featured_image_url,
        featured_image_data=featured_image_data,
        metadata=metadata,
    )


async def _promote_or_skip_existing(
    pool: Any,
    task_id: str,
    *,
    stage_only: bool,
    draft_mode: bool,
    topic: str,
    site_config: SiteConfig,
) -> PublishResult | None:
    """Phase 2 — idempotency / approve-then-publish promotion guard.

    Slugs carry a ``task_id[:8]`` suffix, so an existing post for this task is
    found by suffix match. Behavior matrix:

    - existing.status='published' → idempotent no-op (real duplicate)
    - existing.status='approved' AND stage_only=True → no-op (re-stage)
    - existing.status='approved' AND stage_only=False → PROMOTE in place to
      'published' (+ published_at / distributed_at), sync ``pipeline_tasks``,
      push to R2, and return the existing post (no duplicate row created)
    - existing.status='draft' → no-op (mid-edit, leave alone)

    Returns a :class:`PublishResult` to short-circuit the publish, or ``None``
    when no existing post matched and the caller should create one.

    (2026-05-27: the old guard returned "skipping duplicate" for ANY existing
    post, so the two-step approve→publish flow left the row stuck at
    'approved'. The promote branch fixes that; the inline R2 push closes the
    2026-05-27 R2-drift alert where the short-circuit returned before export.)
    """
    _task_suffix = task_id[:8]
    existing = await pool.fetchrow(
        "SELECT id, slug, title, status FROM posts WHERE slug LIKE '%' || $1",
        _task_suffix,
    )
    if not existing:
        return None

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
                # Sync pipeline_tasks.status in lockstep — same rationale as
                # the scheduled_publisher loop. Reads the seam
                # (posts.metadata->>'pipeline_task_id') populated either at
                # insert by publish_post_from_task or by the 2026-05-28 backfill.
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
        # Push to R2 inline. Skipping this is what caused the 2026-05-27 R2
        # drift alert — the short-circuit returned before reaching the main
        # path's export_post call, leaving R2 one post stale until the
        # static_export_reconciliation probe caught up.
        promote_export_success = False
        try:
            from services.static_export_service import export_post

            promote_export_success = await export_post(
                pool, existing["slug"], site_config=site_config,
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
        # Bust Vercel's cached index renders (/, /archive, /posts,
        # /sitemap.xml, /feed.xml) + the slug page so the promoted post
        # appears in navigation and the sitemap — not just on its own URL.
        # The 2026-05-27 fix added the inline export_post above but missed
        # the matching ISR revalidate the main publish path runs (#327), so
        # `tasks publish` on an approved post pushed R2 yet left every index
        # page stale (Glad-Labs/poindexter#575). _revalidate_isr is
        # non-fatal — a revalidation failure must not undo the publish.
        promote_revalidation_success = await _revalidate_isr(
            existing["slug"], site_config,
        )
        return PublishResult(
            success=True,
            post_id=str(existing["id"]),
            post_slug=existing["slug"],
            published_url=f"/posts/{existing['slug']}",
            post_title=existing.get("title", topic),
            static_export_success=promote_export_success,
            revalidation_success=promote_revalidation_success,
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


async def _resolve_tag_ids(
    pool: Any,
    task: dict[str, Any],
    merged: dict[str, Any],
    seo_keywords: Any,
    task_id: str,
) -> list[str]:
    """Phase 4c — derive post.tag_ids from the task's tags + seo_keywords.

    Normalizes each candidate to slug form, upserts into the ``tags`` table
    (new terms auto-create), and returns the resolved tag ids. Empty/garbage
    input → ``[]``. Tag resolution must never block publishing, so a DB error
    is logged and swallowed (returns ``[]``).
    """
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

    if not candidate_tag_strings:
        return []

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
    if not clean_pairs:
        return []

    tag_ids: list[str] = []
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
        return []
    return tag_ids


async def _upload_media_to_r2_bg(site_config: SiteConfig, post_id: str) -> None:
    """Phase 11e (fire-and-forget) — wait for media files, then upload to R2.

    Waits ``media_upload_delay_seconds`` for podcast/video/short generation to
    finish, uploads each medium to R2, and regenerates the public podcast +
    video RSS feeds on the CDN. Each feed regen is best-effort (non-fatal).
    """
    import asyncio as _aio
    from pathlib import Path

    from services.r2_upload_service import R2UploadService
    _r2 = R2UploadService(site_config=site_config)
    # Give podcast/video/short generation time to complete
    _delay = int(site_config.get("media_upload_delay_seconds", "240"))
    await _aio.sleep(_delay)
    await _r2.upload_podcast_episode(post_id)
    await _r2.upload_video_episode(post_id)
    # Upload short video if it exists
    short_path = Path(os.path.expanduser("~")) / ".poindexter" / "video" / f"{post_id}-short.mp4"
    if short_path.exists():
        await _r2.upload_to_r2(str(short_path), f"video/{post_id}-short.mp4", "video/mp4")
    # Regenerate public podcast RSS feed on R2
    try:
        import httpx as _hx

        from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
        _api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
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
            # Best-effort temp-file cleanup; ignore if it's already gone.
            with suppress(OSError):
                os.unlink(_feed_path)
    except Exception as _e:
        logger.warning("[R2] Podcast feed regen failed (non-fatal): %s", _e)

    # Regenerate public video RSS feed on R2
    try:
        import httpx as _hx

        from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
        _api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
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
            # Best-effort temp-file cleanup; ignore if it's already gone.
            with suppress(OSError):
                os.unlink(_feed_path)
    except Exception as _e:
        logger.warning("[R2] Video feed regen failed (non-fatal): %s", _e)

    # YouTube upload removed in the 2026-05-08 services audit cleanup —
    # the stub adapter raised NotImplementedError and there's no real
    # implementation behind it yet. See poindexter#449 for the OAuth
    # setup checklist; re-wire this branch when the real adapter ships.


async def _send_post_newsletter_bg(
    db_service,
    site_config: SiteConfig,
    title: str,
    excerpt: str,
    slug: str,
) -> None:
    """Phase 11f (fire-and-forget) — email the new post to subscribers."""
    try:
        from services.newsletter_service import send_post_newsletter
        _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
        # #272 Phase-2b/2g: newsletter_service requires a site_config — pass the
        # run-bound instance threaded from publish_post_from_task.
        result = await send_post_newsletter(
            _pool, title, excerpt, slug, site_config=site_config,
        )
        logger.info("[NEWSLETTER] Result: %s", result)
    except Exception as e:
        logger.warning("[NEWSLETTER] Failed (non-fatal): %s", e)


async def _record_edit_distance_metrics(
    db_service,
    *,
    task: dict[str, Any],
    task_metadata: dict[str, Any],
    merged: dict[str, Any],
    draft_content: str,
    task_id: str,
    post_id: str,
    publisher: str,
) -> None:
    """Phase 13 — record the operator's edit distance as an auto-publish signal.

    Pre-approve content is what finalize_task snapshotted; post-approve is what
    shipped. The diff is the gate's primary trust signal per
    ``feedback_auto_publish_requires_edit_distance_track_record``. Best-effort —
    a failure here must never fail a successful publish.
    """
    try:
        from modules.content.auto_publish_gate import record_post_approve_metrics
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


async def _backstamp_media_assets(db_service, task_id: str, post_id: str) -> None:
    """Close the FK on media_assets rows that upstream stages (e.g.
    source_featured_image) recorded with ``post_id=NULL`` before this post
    existed — they carry the producing ``task_id``. Best-effort
    (Glad-Labs/glad-labs-stack#193)."""
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


async def _emit_publish_webhook(db_service, task_id: str, post_title: str) -> None:
    """Phase 7 — emit the ``post.published`` webhook event (best-effort)."""
    try:
        from services.webhook_delivery_service import emit_webhook_event

        await emit_webhook_event(
            getattr(db_service, "cloud_pool", None) or db_service.pool,
            "post.published",
            {"task_id": str(task_id), "title": post_title, "site": "default"},
        )
    except Exception:
        logger.debug("[WEBHOOK] Failed to emit post.published event", exc_info=True)


def _queue_sync_and_embed(
    db_service,
    background_tasks,
    post_id: str,
    post_title: str,
    seo_description: str,
    post_content: Any,
    site_config: "SiteConfig | None" = None,
) -> None:
    """Phase 8 — queue cloud-DB sync + pgvector embed (no-op when hooks off)."""
    if not _should_run_post_publish_hooks():
        return
    post_dict = {
        "id": post_id,
        "title": post_title,
        "excerpt": seo_description,
        "content": post_content,
    }
    if background_tasks:
        background_tasks.add_task(_sync_published_post, post_id)
        background_tasks.add_task(_embed_published_post, db_service, post_dict, site_config)
    else:
        _spawn_background(
            _sync_published_post(post_id), name=f"sync_published_post({post_id})"
        )
        _spawn_background(
            _embed_published_post(db_service, post_dict, site_config),
            name=f"embed_published_post({post_id})",
        )
    logger.info("[publish_service] Queued sync + embed for post %s", post_id)


def _queue_social_distribution(
    background_tasks,
    *,
    task: dict[str, Any],
    slug: str,
    seo_description: str,
    seo_keywords: Any,
    post_title: str,
    site_config: SiteConfig,
    pool=None,
) -> None:
    """Phase 9 — queue social-media post generation + distribution (best-effort).

    ``pool`` is threaded through to ``generate_and_distribute_social_posts`` so
    the row-driven ``publishing_adapters`` dispatch actually runs (#556 — every
    call site previously dropped it, leaving the dispatch loop permanently inert).
    """
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
                    site_config=site_config,
                    pool=pool,
                )
            else:
                _spawn_background(
                    generate_and_distribute_social_posts(
                        title=_title, slug=slug,
                        excerpt=seo_description, keywords=_seo_kw,
                        site_config=site_config,
                        pool=pool,
                    ),
                    name=f"social_posts({slug})",
                )
            logger.info("[SOCIAL] Queued social post generation for %s", slug)
    except Exception as e:
        logger.warning("[SOCIAL] Social posting failed (non-fatal): %s", e)


def _queue_devto_crosspost(
    db_service, background_tasks, post_id: str, site_config: SiteConfig
) -> None:
    """Phase 9b — queue Dev.to cross-posting (best-effort)."""
    try:
        from services.devto_service import DevToCrossPostService

        # Thread the lifespan-bound site_config so the DevTo crosspost can read
        # site_url for the canonical URL; without it the service falls back to a
        # fresh empty SiteConfig and crashes on .require("site_url") — observed
        # during the 2026-05-17 auto-publish stress test.
        devto_svc = DevToCrossPostService(
            getattr(db_service, "cloud_pool", None) or db_service.pool,
            site_config=site_config,
        )
        if background_tasks:
            background_tasks.add_task(devto_svc.cross_post_by_post_id, post_id)
        else:
            _spawn_background(
                devto_svc.cross_post_by_post_id(post_id),
                name=f"devto_crosspost({post_id})",
            )
        logger.info("[DEVTO] Queued cross-post for post %s", post_id)
    except Exception as e:
        logger.warning("[DEVTO] Cross-posting setup failed (non-fatal): %s", e)


async def _revalidate_isr(slug: str, site_config: SiteConfig) -> bool:
    """Phase 10 — trigger ISR revalidation for ``slug``. Returns success.

    Routed through trigger_isr_revalidate (#327) so every publish path uses the
    same canonical-paths/tags + async get_secret() flow."""
    try:
        from services.revalidation_service import trigger_isr_revalidate

        ok = await trigger_isr_revalidate(slug, site_config=site_config)
        if not ok:
            logger.warning("[publish_service] ISR revalidation returned failure for %s", slug)
        return ok
    except Exception as reval_err:
        logger.warning("[publish_service] ISR revalidation error (non-fatal): %s", reval_err)
        return False


async def _export_static_post(db_service, slug: str, site_config: SiteConfig) -> bool:
    """Phase 10b — synchronous static JSON export to R2. Returns success.

    Awaited inline (not fire-and-forget): a cancelled background task silently
    froze R2 between 2026-05-08 and 2026-05-11 (four posts never reached the
    bucket). ~3-5s on R2 is within the publish-endpoint latency budget; the
    result lands on PublishResult.static_export_success so callers can surface
    the failure."""
    try:
        from services.static_export_service import export_post

        _pool = getattr(db_service, "cloud_pool", None) or db_service.pool
        ok = await export_post(_pool, slug, site_config=site_config)
        if ok:
            logger.info("[STATIC_EXPORT] Synchronous export complete for %s", slug)
        else:
            logger.warning(
                "[STATIC_EXPORT] export_post returned failure for %s — "
                "R2 index will be stale until the reconciliation watchdog "
                "fires or the next successful publish completes",
                slug,
            )
        return ok
    except Exception as e:
        logger.error(
            "[STATIC_EXPORT] export_post raised for %s: %s",
            slug, e, exc_info=True,
        )
        return False


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
    # 1. Parse task data (result + task_metadata merged) — phase 1
    # ---------------------------------------------------------------
    _parsed = _parse_publish_inputs(task, task_id)
    if isinstance(_parsed, PublishResult):
        return _parsed  # missing content/topic — already logged
    merged = _parsed.merged
    task_metadata = _parsed.task_metadata
    topic = _parsed.topic
    draft_content = _parsed.draft_content
    seo_description = _parsed.seo_description
    seo_keywords = _parsed.seo_keywords
    featured_image_url = _parsed.featured_image_url
    featured_image_data = _parsed.featured_image_data
    metadata = _parsed.metadata

    # ---------------------------------------------------------------
    # 1b. Idempotency / approve-then-publish promotion guard — phase 2
    # ---------------------------------------------------------------
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool
    _existing_result = await _promote_or_skip_existing(
        pool,
        task_id,
        stage_only=stage_only,
        draft_mode=draft_mode,
        topic=topic,
        site_config=_sc,
    )
    if _existing_result is not None:
        return _existing_result

    # ---------------------------------------------------------------
    # 1c. Niche allowlist gate (#729) -- a post whose task has no
    # resolved/active niche must never reach readers. ``auto_publish_gate``
    # already blocks the auto path; this is the hard backstop on the
    # manual approve/publish path. Drafts (WIP) are exempt; already-
    # published promotions returned above. Toggle via
    # ``enforce_niche_allowlist`` (default true).
    # ---------------------------------------------------------------
    if _sc.get_bool("enforce_niche_allowlist", True) and not draft_mode:
        from services.niche_service import get_active_niche_slugs

        _task_niche = (task.get("niche_slug") or "").strip()
        _allowed = await get_active_niche_slugs(pool)
        # Fail-open on an empty allowlist (niches table unreadable) so a
        # transient DB error can't halt all publishing; block only when we
        # positively know the niche isn't active.
        if _allowed and _task_niche not in _allowed:
            from services.integrations.operator_notify import notify_operator
            _msg = (
                f"publish blocked (#729): task {task_id} "
                f"niche={_task_niche or '<none>'} not in active "
                f"allowlist {sorted(_allowed)}"
            )
            logger.error("[publish_service] %s", _msg)
            with suppress(Exception):
                await notify_operator(_msg, critical=False, site_config=_sc)
            return PublishResult(success=False, error=_msg)

    # ---------------------------------------------------------------
    # 2. Extract title from content (LLM often puts # Title at top)
    # ---------------------------------------------------------------
    extracted_title, cleaned_content = extract_title_from_content(draft_content)
    post_title = extracted_title or merged.get("title") or topic
    # Strip leaked test-harness batch/debug suffixes BEFORE the slug is
    # derived (issue #4) so neither the title nor the slug carries them.
    post_title = sanitize_published_title(post_title)
    post_content = cleaned_content

    logger.info("[publish_service] Post title: %s", post_title)
    logger.info("[publish_service] Extracted from content: %s", bool(extracted_title))
    logger.info("[publish_service] Content length: %d chars", len(post_content or ""))

    # ---------------------------------------------------------------
    # 3. Create slug
    # ---------------------------------------------------------------
    slug = build_post_slug(post_title, task_id)

    # ---------------------------------------------------------------
    # 4. Get author + category
    # ---------------------------------------------------------------
    from services.category_resolver import select_category_for_topic
    from services.default_author import get_or_create_default_author

    author_id = await get_or_create_default_author(db_service)
    category_id = await select_category_for_topic(post_title, db_service)

    # ---------------------------------------------------------------
    # 4c. Resolve tags — phase 4c
    # ---------------------------------------------------------------
    tag_ids = await _resolve_tag_ids(pool, task, merged, seo_keywords, task_id)

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
    _strict_mode_status = "published"
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
    try:
        _media_pool = getattr(db_service, "pool", None)
        if _media_pool is not None:
            media_to_generate = await resolve_media_to_generate(
                _media_pool, niche_slug
            )
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
        "excerpt": choose_excerpt(
            task_metadata=task_metadata,
            merged=merged,
            seo_description=seo_description,
            title=post_title,
        ),
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

    # Back-stamp media_assets rows recorded with post_id=NULL before insert.
    await _backstamp_media_assets(db_service, task_id, post_id)

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
    # 7. Emit webhook — phase 7
    # ---------------------------------------------------------------
    await _emit_publish_webhook(db_service, task_id, post_title)

    # ---------------------------------------------------------------
    # 8. Sync to cloud DB + embed in pgvector — phase 8
    # ---------------------------------------------------------------
    _queue_sync_and_embed(
        db_service, background_tasks, post_id, post_title, seo_description, post_content,
        site_config=site_config,
    )

    # ---------------------------------------------------------------
    # 9. Queue social media post generation — phase 9
    # ---------------------------------------------------------------
    if queue_social:
        _queue_social_distribution(
            background_tasks,
            task=task,
            slug=slug,
            seo_description=seo_description,
            seo_keywords=seo_keywords,
            post_title=post_title,
            site_config=_sc,
            pool=getattr(db_service, "pool", None),
        )

    # ---------------------------------------------------------------
    # 9b. Queue Dev.to cross-posting (fire-and-forget) — phase 9b
    # ---------------------------------------------------------------
    _queue_devto_crosspost(db_service, background_tasks, post_id, _sc)

    # ---------------------------------------------------------------
    # 10. ISR revalidation — phase 10
    # ---------------------------------------------------------------
    revalidation_success = False
    if trigger_revalidation:
        revalidation_success = await _revalidate_isr(slug, _sc)

    # ---------------------------------------------------------------
    # 10b. Static JSON export to CDN (inline) — phase 10b
    # ---------------------------------------------------------------
    # The export is awaited inline (see the helper docstring for why
    # fire-and-forget froze R2).
    static_export_success = False
    static_export_success = await _export_static_post(db_service, slug, _sc)

    # ---------------------------------------------------------------
    # 11. Ping search engines (fire-and-forget)
    # ---------------------------------------------------------------
    site_url = _sc.require("site_url")
    published_url_full = f"{site_url}/posts/{slug}"
    _spawn_background(
        _ping_search_engines(site_url, published_url_full, site_config=_sc),
        name=f"ping_search_engines({slug})",
    )

    # ---------------------------------------------------------------
    # 11b/c/d. Derived media (podcast / video / short) — REMOVED.
    # ---------------------------------------------------------------
    # Media generation no longer fires from the publish path.
    # It is now the backfill jobs' responsibility. The 11e R2-upload
    # hook below remains (it uploads whatever media files exist; a no-op
    # when none are present).

    # ---------------------------------------------------------------
    # 11e. Upload media to R2 CDN (fire-and-forget, after generation)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        _spawn_background(
            _upload_media_to_r2_bg(_sc, post_id), name=f"upload_media_r2({post_id})"
        )

    # ---------------------------------------------------------------
    # 11f. Newsletter to subscribers (fire-and-forget)
    # ---------------------------------------------------------------
    if _should_run_post_publish_hooks():
        _spawn_background(
            _send_post_newsletter_bg(
                db_service, _sc, post_title, seo_description, slug
            ),
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
    # 13. Edit-distance metrics — auto_publish_gate training signal — phase 13
    # ---------------------------------------------------------------
    await _record_edit_distance_metrics(
        db_service,
        task=task,
        task_metadata=task_metadata,
        merged=merged,
        draft_content=draft_content,
        task_id=task_id,
        post_id=post_id,
        publisher=publisher,
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
    seo_description = post_row["excerpt"] or ""
    seo_keywords_str = post_row["seo_keywords"] or ""
    seo_keywords = [k.strip() for k in seo_keywords_str.split(",") if k.strip()]
    # ``post_content`` / ``media_to_generate`` are no longer read here —
    # media generation moved to the gate driver (poindexter#24).

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
                pool=pool,
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

    # 4. Per-medium generation — REMOVED (Glad-Labs/poindexter#24).
    # Media generation is now the gate driver's job, fired PRE-publish per
    # medium gate (services/jobs/drive_media_gates.py), so a post's media
    # already exists by the time its gates clear and this re-fire runs.
    # This path now only re-fires distribution (social/devto/static/RSS).

    logger.info(
        "[publish_service] Re-fired %d distribution hook(s) for post %s: %s",
        len(fired["hooks"]), post_id, fired["hooks"],
    )
    return fired


async def publish_now(
    pool: Any,
    post_id: str,
    *,
    site_config: SiteConfig | None = None,
) -> dict[str, Any]:
    """Publish an ``approved`` post + fire distribution.

    The explicit publish+distribute entrypoint for the ``tasks publish`` path.
    Flips ``status`` ``approved``/``awaiting_gates`` → ``published`` (+
    ``published_at`` / ``distributed_at`` / ``pipeline_tasks`` status sync,
    one transaction), then re-fires distribution (static export / ISR /
    social / dev.to / search-engine ping).

    Refuses (idempotent no-op) when the post isn't in a publishable state
    (already published / wrong status). ``site_config`` is resolved from
    the process container when not injected.
    """
    if site_config is None:
        try:
            from services.container_registry import get_container
            _c = get_container()
            site_config = _c.site_config if _c is not None else SiteConfig()
        except Exception:
            site_config = SiteConfig()
    _sc = site_config

    fired: dict[str, Any] = {"published": False, "post_id": post_id, "hooks": []}

    async with pool.acquire() as conn:
        post_row = await conn.fetchrow(
            """
            SELECT id::text AS id, title, slug, excerpt, seo_keywords
              FROM posts
             WHERE id::text = $1
            """,
            str(post_id),
        )
    if post_row is None:
        fired["reason"] = "post_not_found"
        return fired

    post_title = post_row["title"]
    slug = post_row["slug"]
    seo_description = post_row["excerpt"] or ""
    seo_keywords = [k.strip() for k in (post_row["seo_keywords"] or "").split(",") if k.strip()]

    # Flip approved/awaiting_gates → published + sync the linked task, in one
    # transaction. The seam (metadata->>'pipeline_task_id') keeps
    # pipeline_tasks.status in lockstep (same pattern as the promote path).
    async with pool.acquire() as conn:
        async with conn.transaction():
            update_result = await conn.execute(
                """
                UPDATE posts
                   SET status = 'published',
                       published_at = COALESCE(published_at, NOW()),
                       distributed_at = COALESCE(distributed_at, NOW()),
                       updated_at = NOW()
                 WHERE id::text = $1
                   AND status IN ('approved', 'awaiting_gates')
                """,
                str(post_id),
            )
            await conn.execute(
                """
                UPDATE pipeline_tasks pt
                   SET status = 'published', updated_at = NOW()
                  FROM posts p
                 WHERE p.id::text = $1
                   AND p.metadata ->> 'pipeline_task_id' = pt.task_id
                   AND pt.status IN ('approved', 'scheduled')
                """,
                str(post_id),
            )
    if not update_result.startswith("UPDATE 1"):
        # Already published or not in a publishable state — idempotent no-op.
        logger.info(
            "[publish_service] publish_now: post %s not in approved/"
            "awaiting_gates (%s) — no status flip", post_id, update_result,
        )
        fired["reason"] = "not_publishable"
        return fired

    fired["published"] = True
    logger.info("[publish_service] publish_now: post %s → published", post_id)

    # ---- Distribution (best-effort; media-gen intentionally excluded) ----
    try:
        from services.static_export_service import export_post
        if await export_post(pool, slug, site_config=_sc):
            fired["hooks"].append("static_export")
    except Exception as e:
        logger.warning("[publish_service] publish_now static_export failed (non-fatal): %s", e)

    try:
        from services.revalidation_service import trigger_isr_revalidate
        if await trigger_isr_revalidate(slug, site_config=_sc):
            fired["hooks"].append("isr_revalidate")
    except Exception as e:
        logger.warning("[publish_service] publish_now ISR revalidate failed (non-fatal): %s", e)

    try:
        from services.social_poster import generate_and_distribute_social_posts
        _spawn_background(
            generate_and_distribute_social_posts(
                title=post_title, slug=slug,
                excerpt=seo_description, keywords=seo_keywords,
                site_config=_sc,
                pool=pool,
            ),
            name=f"social_posts({slug})",
        )
        fired["hooks"].append("social")
    except Exception as e:
        logger.warning("[SOCIAL] publish_now social failed (non-fatal): %s", e)

    try:
        from services.devto_service import DevToCrossPostService
        devto_svc = DevToCrossPostService(pool, site_config=_sc)
        _spawn_background(
            devto_svc.cross_post_by_post_id(post_id),
            name=f"devto_crosspost({post_id})",
        )
        fired["hooks"].append("devto")
    except Exception as e:
        logger.warning("[DEVTO] publish_now devto failed (non-fatal): %s", e)

    try:
        site_url = _sc.require("site_url")
        _spawn_background(
            _ping_search_engines(site_url, f"{site_url}/posts/{slug}", site_config=_sc),
            name=f"ping_search_engines({slug})",
        )
        fired["hooks"].append("search_engines")
    except Exception as e:
        logger.warning("[SEO] publish_now search ping failed (non-fatal): %s", e)

    logger.info(
        "[publish_service] publish_now fired %d distribution hook(s) for post %s: %s",
        len(fired["hooks"]), post_id, fired["hooks"],
    )
    return fired
