"""MediaDistributeJob — Stage-2 link + Gate-2-seed pass (#689 Plan 8 / 8b-2).

The ``media_pipeline`` persists task-keyed ``media_assets`` rows (``video_long`` /
``video_short``) with ``post_id=NULL`` — at render time the ``posts`` row may not
exist yet (it's created at publish). This scheduled job is the bridge from that
task-keyed render output to the post-keyed Gate-2 distribution world:

1. **Link.** For each unlinked media_pipeline asset, resolve the post via the
   canonical seam ``posts.metadata->>'pipeline_task_id'`` and back-stamp
   ``media_assets.post_id``. Assets whose task hasn't been published yet are
   left for a later cycle.
2. **Seed Gate 2.** Record a ``media_approvals`` pending row so the asset
   surfaces in the operator's Gate-2 review queue — ``video`` for the long form,
   ``video_short`` for the short (the media_approvals media vocabulary; the
   matching media_assets *types* are ``video_long`` / ``video_short``).
3. **Dispatch.** Deliver Gate-2-*approved* assets to the enabled video platforms
   via the publishing handler registry — long form ``shorts=False``, short
   ``shorts=True`` (the #682/#1249 Shorts-aware YouTube handler) — then stamp
   ``record_dispatched`` and **capture the external handles**: on a successful
   upload the handler's external video id + public url are persisted via
   ``_persist_dispatch_result`` — merged into ``media_assets.platform_video_ids``
   (``{"youtube": "<videoId>"}``, a non-clobbering jsonb merge) and upserted as a
   ``pipeline_distributions`` row (``target='youtube'``, ``external_id``,
   ``external_url``, ``status='published'``) in the same transaction as the
   ``record_dispatched`` stamp. Without this the id/url were discarded and there
   was no record of what landed on YouTube (or any handle to dedupe re-uploads).
   Runs the same cycle as link/seed so a freshly-approved asset can reach YouTube
   without waiting a cycle.

**Deconfliction with ``backfill_videos``.** That legacy dispatch-only pass fires
only for posts whose ``VIDEO_DIR/{post_id}.mp4`` exists on disk; media_pipeline
assets live task-keyed (``{task_id}.mp4``) at ``media_assets.storage_path``, so
backfill skips them — the approved row stays eligible for this pass alone, no
double-send.

**Default-OFF.** Gated on ``media_pipeline_trigger_enabled`` (the Stage-2 master
switch, default ``false``) so the job is scheduled but a behaviour no-op in prod
until the operator opts the whole lane in. ``idempotent=True``: re-linking is a
no-op (already-linked assets fall out of the query; ``record_pending`` is
``ON CONFLICT DO NOTHING``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from plugins.job import JobResult
from services.media_approval_service import record_dispatched, record_pending

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PlatformDispatchResult:
    """Outcome of delivering one asset to one video platform.

    ``_dispatch_asset`` returns one of these per enabled adapter so the caller
    can both decide the aggregate dispatch outcome (``record_dispatched``) AND
    persist the platform's external handles (``external_id`` / ``url``). The
    upload handler returns the external video id under its ``post_id`` key and
    the public watch URL under ``url`` (see
    ``services/integrations/handlers/publishing_youtube.py``); we capture them
    here under clearer names. ``external_id`` is ``None`` on failure.
    """

    platform: str
    success: bool
    external_id: str | None = None
    url: str | None = None

# Video destinations this lane delivers to. Adding a platform = add here + a
# publishing_adapters row + a publishing.<name> handler (the #112 contract).
_VIDEO_PLATFORMS: frozenset[str] = frozenset({"youtube"})

# media_assets.type → media_approvals.medium. The asset table distinguishes
# long/short by ``video_long`` / ``video_short``; the approvals table uses
# ``video`` for the long form (its historical medium name) and ``video_short``
# for the short. Anything not in this map is not a media_pipeline video asset.
_TYPE_TO_MEDIUM: dict[str, str] = {
    "video_long": "video",
    "video_short": "video_short",
}

# media_pipeline assets awaiting a post link: a rendered video with a task id
# but no post yet. Newest task first so a freshly-published piece links promptly.
_UNLINKED_SQL = """
    SELECT id::text AS id, task_id, type
      FROM media_assets
     WHERE post_id IS NULL
       AND task_id IS NOT NULL
       AND type = ANY($1::text[])
     ORDER BY created_at DESC
     LIMIT $2
"""

# Resolve the post that a Stage-1 task became, via the canonical seam stamped
# by publish_service.publish_post_from_task. NULL until the piece is published.
_RESOLVE_POST_SQL = """
    SELECT id::text
      FROM posts
     WHERE metadata->>'pipeline_task_id' = $1
     ORDER BY published_at DESC NULLS LAST
     LIMIT 1
"""

# Back-stamp the resolved post id onto the asset row (idempotent — the row drops
# out of _UNLINKED_SQL once post_id is set).
_LINK_SQL = "UPDATE media_assets SET post_id = $1::uuid, updated_at = NOW() WHERE id = $2::uuid"

# Approved-but-undispatched media_pipeline assets, joined to the durable file
# path. The CASE maps the media_approvals medium back to the media_assets type
# (video → video_long, video_short → video_short) so we deliver the right file.
# Deconfliction with backfill_videos: that legacy pass only fires for posts
# whose VIDEO_DIR/{post_id}.mp4 exists on disk; media_pipeline assets live
# task-keyed ({task_id}.mp4) at media_assets.storage_path, so backfill skips
# them and the approved row stays eligible for this pass alone — no double-send.
_APPROVED_UNDISPATCHED_SQL = """
    SELECT ma.post_id::text AS post_id,
           ma.medium,
           p.title, p.content, p.excerpt, p.seo_keywords, p.slug,
           mas.id::text AS asset_id,
           mas.task_id,
           mas.storage_path
      FROM media_approvals ma
      JOIN posts p ON p.id = ma.post_id
      JOIN media_assets mas
        ON mas.post_id = ma.post_id
       AND mas.type = CASE ma.medium
                          WHEN 'video' THEN 'video_long'
                          WHEN 'video_short' THEN 'video_short'
                      END
     WHERE ma.status = 'approved'
       AND ma.dispatched_at IS NULL
       AND ma.medium = ANY($1::text[])
       AND COALESCE(mas.storage_path, '') <> ''
     ORDER BY ma.created_at ASC
     LIMIT $2
"""

# Enabled video-platform adapter rows (the registry routes the handler by name).
_ADAPTERS_SQL = """
    SELECT name, platform, handler_name, config, metadata
      FROM publishing_adapters
     WHERE enabled = true
       AND platform = ANY($1::text[])
"""

# Merge one platform's external video id into the asset's platform_video_ids
# jsonb. The ``||`` concat operator is a shallow merge, so {"youtube": "<id>"}
# replaces only the youtube key and leaves any other platform's id intact —
# the asset can be cross-posted without one delivery clobbering another's id.
_MERGE_PLATFORM_VIDEO_ID_SQL = """
    UPDATE media_assets
       SET platform_video_ids = platform_video_ids || $2::jsonb,
           updated_at = NOW()
     WHERE id = $1::uuid
"""

# Record the external delivery for observability + dedupe. ``task_id`` is the
# media_assets task key (== posts.metadata->>'pipeline_task_id', the FK target
# for pipeline_distributions). Upserted on the (task_id, target) unique key so a
# re-dispatch refreshes the same row rather than duplicating it.
_RECORD_DISTRIBUTION_SQL = """
    INSERT INTO pipeline_distributions
        (task_id, target, status, external_id, external_url, post_id, published_at)
    VALUES ($1, $2, 'published', $3, $4, $5::uuid, NOW())
    ON CONFLICT (task_id, target) DO UPDATE SET
        status       = EXCLUDED.status,
        external_id  = COALESCE(EXCLUDED.external_id, pipeline_distributions.external_id),
        external_url = COALESCE(EXCLUDED.external_url, pipeline_distributions.external_url),
        post_id      = COALESCE(EXCLUDED.post_id, pipeline_distributions.post_id),
        published_at = COALESCE(EXCLUDED.published_at, pipeline_distributions.published_at)
"""


async def _dispatch_asset(
    pool: Any, site_config: Any, row: dict[str, Any], *, shorts: bool
) -> list[_PlatformDispatchResult]:
    """Deliver one approved video asset to the enabled video platforms.

    Builds the SEO-rich YouTube payload from the post's structured fields
    (reusing the same helpers backfill_videos uses, glad-labs-stack#275) and
    threads the ``shorts`` flag so the adapter injects the ``#Shorts`` marker for
    short-form (the #682/#1249 handler). Per-adapter exceptions are isolated.

    Returns one ``_PlatformDispatchResult`` per enabled adapter, carrying the
    handler's external video id + public url on success so the caller can
    persist them (``media_assets.platform_video_ids`` + ``pipeline_distributions``).
    Previously this returned a bare ``bool`` and the id/url were discarded — the
    bug this fix closes. An empty list means there was nothing to dispatch to
    (handler load failed / no enabled adapter); the caller treats that as a
    failed attempt, same as before.
    """
    # Reuse the pure YouTube-payload helpers — they compose the description
    # (SEO excerpt + canonical back-link + stripped body, ≤4800 chars) and parse
    # seo_keywords into capped tags. (Shared home: services/jobs/backfill_videos.)
    from services.integrations import registry
    from services.integrations.handlers import load_all
    from services.jobs.backfill_videos import (
        _build_youtube_description,
        _parse_seo_keywords,
    )

    try:
        load_all()  # idempotent — ensures publishing_youtube is registered
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MEDIA_DISTRIBUTE] handler load failed: %s", exc)
        return []

    try:
        adapters = await pool.fetch(_ADAPTERS_SQL, list(_VIDEO_PLATFORMS))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MEDIA_DISTRIBUTE] adapter lookup failed: %s", exc)
        return []
    if not adapters:
        logger.debug("[MEDIA_DISTRIBUTE] no enabled video adapters — skipping")
        return []

    description = _build_youtube_description(
        seo_description=row.get("excerpt") or "",
        body=row.get("content") or "",
        site_config=site_config,
        slug=row.get("slug") or "",
    )
    tags = _parse_seo_keywords(row.get("seo_keywords") or "")
    payload = {
        "media_path": row["storage_path"],
        "title": row.get("title") or "",
        "description": description,
        "tags": tags or None,
        "post_id": row["post_id"],
        "shorts": shorts,
    }

    results: list[_PlatformDispatchResult] = []
    for adapter in adapters:
        platform = adapter["platform"]
        try:
            result = await registry.dispatch(
                "publishing",
                adapter["handler_name"] or platform,
                payload,
                site_config=site_config,
                row=dict(adapter),
                pool=pool,
            )
            if isinstance(result, dict) and result.get("success"):
                # The handler returns the external video id under "post_id"
                # and the public watch URL under "url" — capture both so the
                # caller can persist them.
                results.append(
                    _PlatformDispatchResult(
                        platform=platform,
                        success=True,
                        external_id=result.get("post_id"),
                        url=result.get("url"),
                    )
                )
                logger.info(
                    "[MEDIA_DISTRIBUTE] %s upload succeeded for post %s "
                    "(shorts=%s, external_id=%s)",
                    platform, row["post_id"], shorts, result.get("post_id"),
                )
            else:
                results.append(
                    _PlatformDispatchResult(platform=platform, success=False)
                )
                logger.warning(
                    "[MEDIA_DISTRIBUTE] %s upload failed for post %s: %s",
                    platform, row["post_id"],
                    (result or {}).get("error") if isinstance(result, dict) else result,
                )
        except Exception as exc:  # noqa: BLE001 — one platform must not starve others
            results.append(
                _PlatformDispatchResult(platform=platform, success=False)
            )
            logger.warning(
                "[MEDIA_DISTRIBUTE] %s upload raised for post %s: %s",
                platform, row["post_id"], exc,
            )
    return results


async def _persist_dispatch_result(
    pool: Any,
    *,
    post_id: str,
    medium: str,
    asset_id: str | None,
    task_id: str | None,
    results: list[_PlatformDispatchResult],
) -> None:
    """Stamp the dispatch outcome AND capture each platform's external handles.

    All writes happen in a single transaction so a successful upload is never
    recorded as dispatched (``media_approvals``) without its external id/url,
    and vice-versa. Per successful platform we:

    1. merge ``{platform: external_id}`` into ``media_assets.platform_video_ids``
       (shallow ``||`` merge — never clobbers another platform's id), and
    2. upsert a ``pipeline_distributions`` row (``status='published'``) carrying
       ``external_id`` / ``external_url`` for observability + re-upload dedupe.

    Both writes are skipped for a platform with no ``external_id`` (a failed
    upload, or a handler that returned success without an id), and the
    distribution upsert is skipped when ``task_id`` is missing (it's the NOT
    NULL FK key for ``pipeline_distributions``). ``record_dispatched`` still
    records the failed attempt in those cases so the row stays re-dispatchable.
    """
    ok = any(r.success for r in results)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await record_dispatched(conn, post_id, medium, success=ok)
            for r in results:
                if not (r.success and r.external_id):
                    continue
                if asset_id:
                    await conn.execute(
                        _MERGE_PLATFORM_VIDEO_ID_SQL,
                        asset_id,
                        json.dumps({r.platform: r.external_id}),
                    )
                if task_id:
                    await conn.execute(
                        _RECORD_DISTRIBUTION_SQL,
                        task_id, r.platform, r.external_id, r.url, post_id,
                    )
                else:
                    logger.warning(
                        "[MEDIA_DISTRIBUTE] %s delivered post %s (external_id=%s) "
                        "but no task_id — skipping pipeline_distributions row",
                        r.platform, post_id, r.external_id,
                    )


def _max_per_cycle(site_config: Any) -> int:
    try:
        return max(1, int(site_config.get("media_distribute_max_per_cycle", "20") or "20"))
    except (TypeError, ValueError):
        return 20


class MediaDistributeJob:
    name = "media_distribute"
    description = (
        "Link media_pipeline-rendered assets to their published post, seed "
        "Gate-2 approval rows, and dispatch approved assets to video platforms "
        "(long + Shorts; dormant until media_pipeline_trigger_enabled)"
    )
    schedule = "every 10 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        # Stage-2 master switch — default OFF. Checked before any DB work.
        if not sc.get_bool("media_pipeline_trigger_enabled", False):
            return JobResult(
                ok=True,
                detail="media_pipeline_trigger_enabled=false — dormant",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=True, detail="no pool — skipping", changes_made=0)

        limit = _max_per_cycle(sc)
        try:
            rows = await pool.fetch(_UNLINKED_SQL, list(_TYPE_TO_MEDIUM.keys()), limit)
        except Exception as exc:  # noqa: BLE001 — a query failure must not crash the scheduler
            logger.warning("[MEDIA_DISTRIBUTE] unlinked-asset query failed: %s", exc)
            return JobResult(ok=False, detail=f"query failed: {exc}", changes_made=0)

        linked = 0
        for row in rows or []:
            asset_id = row["id"]
            task_id = row["task_id"]
            medium = _TYPE_TO_MEDIUM.get(row["type"])
            if medium is None:
                continue

            try:
                post_id = await pool.fetchval(_RESOLVE_POST_SQL, str(task_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEDIA_DISTRIBUTE] post resolve failed for task %s: %s",
                    task_id, exc,
                )
                continue
            if not post_id:
                # Task not published yet — leave the asset unlinked for a later
                # cycle (the post is created at publish, which may lag approval).
                continue

            try:
                await pool.execute(_LINK_SQL, post_id, asset_id)
                await record_pending(pool, post_id, medium)
                linked += 1
                logger.info(
                    "[MEDIA_DISTRIBUTE] linked asset %s (%s) → post %s + seeded "
                    "Gate-2 %s",
                    asset_id, row["type"], post_id, medium,
                )
            except Exception as exc:  # noqa: BLE001 — one asset must not halt the pass
                logger.warning(
                    "[MEDIA_DISTRIBUTE] link/seed failed for asset %s → post %s: %s",
                    asset_id, post_id, exc,
                )

        # --- Dispatch pass: deliver approved, undispatched assets ------------
        # Fires only for Gate-2-approved rows, so a freshly-approved asset can
        # reach YouTube the same cycle. Long form → shorts=False, short →
        # shorts=True (the #1249 Shorts-aware handler).
        dispatched = 0
        try:
            drows = await pool.fetch(
                _APPROVED_UNDISPATCHED_SQL,
                list(_TYPE_TO_MEDIUM.values()),
                limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MEDIA_DISTRIBUTE] approved-undispatched query failed: %s", exc)
            drows = []

        for row in drows or []:
            medium = row["medium"]
            path = row.get("storage_path") or ""
            if not path or not os.path.exists(path):
                # Durable file gone (cleaned up / never landed). Don't stamp —
                # leave the approved row eligible for the reconciliation watchdog.
                logger.warning(
                    "[MEDIA_DISTRIBUTE] durable file missing for post %s (%s): %s "
                    "— skipping (left for reconciliation)",
                    row["post_id"], medium, path,
                )
                continue

            shorts = medium == "video_short"
            try:
                results = await _dispatch_asset(pool, sc, dict(row), shorts=shorts)
            except Exception as exc:  # noqa: BLE001 — one asset must not halt the pass
                logger.warning(
                    "[MEDIA_DISTRIBUTE] dispatch raised for post %s (%s): %s",
                    row["post_id"], medium, exc,
                )
                results = []
            ok = any(r.success for r in results)

            # Stamp the dispatch + capture each platform's external id/url in one
            # transaction (the id/url used to be discarded — the bug). asset_id /
            # task_id come off the approved-undispatched row.
            try:
                await _persist_dispatch_result(
                    pool,
                    post_id=row["post_id"],
                    medium=medium,
                    asset_id=row.get("asset_id"),
                    task_id=row.get("task_id"),
                    results=results,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[MEDIA_DISTRIBUTE] record dispatch outcome failed for post %s (%s): %s",
                    row["post_id"], medium, exc,
                )
            if ok:
                dispatched += 1

        detail = f"linked {linked}, dispatched {dispatched}"
        return JobResult(ok=True, detail=detail, changes_made=linked + dispatched)


__all__ = ["MediaDistributeJob"]
