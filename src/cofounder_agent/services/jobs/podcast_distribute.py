"""PodcastDistributeJob — Stage-3 link + Gate-2-seed + RSS distribution (#689).

The podcast twin of ``media_distribute``, kept as a separate single-purpose lane
(the podcast/video split). Three passes per cycle:

1. **Link.** Resolve unlinked task-keyed podcast assets to their published post
   (``posts.metadata->>'pipeline_task_id'``), back-stamp ``post_id``, and seed
   the Gate-2 ``media_approvals(medium='podcast')`` row.
2. **Heal.** Seed ``media_approvals`` for any podcast asset that already has a
   ``post_id`` but no approval row — this picks up the backlog the
   ``media_reconciliation`` watchdog wrote without seeding (the fix for the
   2026-05-28 Spotify freeze).
3. **Deliver.** For Gate-2-*approved* podcast assets, upload the MP3 to R2 at the
   deterministic ``podcast/{cdn_ver}/{post_id}.mp3`` key, stamp the asset URL,
   ``record_dispatched``, and rebuild ``podcast/feed.xml`` on R2 once per cycle.

Gated on ``podcast_pipeline_trigger_enabled`` (default off). ``record_pending``
is ``ON CONFLICT DO NOTHING`` so re-seeding is a no-op (idempotent).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from plugins.job import JobResult
from services.media_approval_service import record_dispatched, record_pending
from utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Unlinked task-keyed podcast assets: rendered but not yet tied to a post.
# storage_path feeds the Layer-1 quality eval at seed time (poindexter#816).
_UNLINKED_PODCAST_SQL = """
    SELECT id::text AS id, task_id, storage_path
      FROM media_assets
     WHERE post_id IS NULL
       AND task_id IS NOT NULL
       AND type = 'podcast'
     ORDER BY created_at DESC
     LIMIT $1
"""

# Resolve the post a Stage-1 task became (NULL until published).
_RESOLVE_POST_SQL = """
    SELECT id::text
      FROM posts
     WHERE metadata->>'pipeline_task_id' = $1
     ORDER BY published_at DESC NULLS LAST
     LIMIT 1
"""

_LINK_SQL = "UPDATE media_assets SET post_id = $1::uuid, updated_at = NOW() WHERE id = $2::uuid"

# Link-time guard (#884, mirrors media_distribute): a post holds at most one
# podcast asset (uniq_media_assets_post_podcast_type). Before back-stamping a
# freshly-rendered task-keyed asset, check the post doesn't already have one —
# else the link UPDATE would violate the unique index and the asset would retry
# forever.
_EXISTING_PODCAST_SQL = """
    SELECT 1 FROM media_assets
     WHERE post_id = $1::uuid AND type = 'podcast'
     LIMIT 1
"""

# Prune an orphan render row blocked by the one-podcast-per-post guard. The file
# on disk is retention-managed, so only the DB row is removed. The post_id IS NULL
# guard prevents deleting a linked asset if the existence check somehow races.
_PRUNE_ORPHAN_SQL = (
    "DELETE FROM media_assets WHERE id = $1::uuid AND post_id IS NULL"
)

# Backlog heal: podcast assets with a resolved post but NO approval row yet.
# DISTINCT ON keeps one row per post (newest render) so its storage_path can
# feed the Layer-1 quality eval at seed time (poindexter#816).
_UNAPPROVED_LINKED_SQL = """
    SELECT DISTINCT ON (ma.post_id)
           ma.post_id::text AS post_id, ma.storage_path
      FROM media_assets ma
     WHERE ma.type = 'podcast'
       AND ma.post_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM media_approvals mv
            WHERE mv.post_id = ma.post_id AND mv.medium = 'podcast'
       )
     ORDER BY ma.post_id, ma.created_at DESC
     LIMIT $1
"""

# Approved, undispatched podcast assets joined to the durable local file. A post
# with duplicate podcast rows would otherwise return one row per duplicate and
# upload each to the same post-keyed R2 key (last-write-wins, non-deterministic)
# — so DISTINCT ON (post_id) + ORDER BY created_at DESC collapses to the single
# newest render, guaranteeing delivery never publishes a stale one (#884). The
# outer wrapper restores approval-time (FIFO) ordering + the cycle LIMIT. Mirrors
# _UNAPPROVED_LINKED_SQL and media_distribute._APPROVED_UNDISPATCHED_SQL.
_APPROVED_UNDISPATCHED_SQL = """
    SELECT * FROM (
        SELECT DISTINCT ON (ma.post_id)
               ma.post_id::text AS post_id,
               ma.created_at AS _appr_created,
               mas.storage_path,
               mas.url
          FROM media_approvals ma
          JOIN media_assets mas
            ON mas.post_id = ma.post_id AND mas.type = 'podcast'
         WHERE ma.medium = 'podcast'
           AND ma.status = 'approved'
           AND ma.dispatched_at IS NULL
         ORDER BY ma.post_id, mas.created_at DESC
    ) t
    ORDER BY t._appr_created ASC
    LIMIT $1
"""

_STAMP_URL_SQL = "UPDATE media_assets SET url = $1, storage_provider = 'cloudflare_r2', updated_at = NOW() WHERE post_id = $2::uuid AND type = 'podcast'"


def _max_per_cycle(site_config: Any) -> int:
    try:
        return max(1, int(site_config.get("podcast_distribute_max_per_cycle", "20") or "20"))
    except (TypeError, ValueError):
        return 20


async def _deliver_podcast(pool: Any, site_config: Any, row: dict[str, Any]) -> bool:
    """Upload one approved podcast MP3 to its post-keyed R2 URL + mark dispatched."""
    post_id = row["post_id"]
    storage_path = row.get("storage_path") or ""
    if not storage_path or not os.path.exists(storage_path):
        logger.warning("[PODCAST_DISTRIBUTE] missing local file for post %s — skip", post_id)
        return False

    cdn_ver = site_config.get("podcast_cdn_version", "v2")
    key = f"podcast/{cdn_ver}/{post_id}.mp3"
    try:
        from services.r2_upload_service import R2UploadService

        r2_svc = R2UploadService(site_config=site_config)
        url = await r2_svc.upload_to_r2(storage_path, key, "audio/mpeg")
    except Exception as exc:  # noqa: BLE001 — one asset must not halt the pass
        logger.warning("[PODCAST_DISTRIBUTE] R2 upload failed for post %s: %s", post_id, exc)
        return False
    if not url:
        return False

    try:
        await pool.execute(_STAMP_URL_SQL, url, post_id)
        await record_dispatched(pool, post_id, "podcast", success=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PODCAST_DISTRIBUTE] stamp/dispatch failed for post %s: %s", post_id, exc)
        return False
    logger.info("[PODCAST_DISTRIBUTE] delivered podcast for post %s → %s", post_id, url)
    return True


async def _rebuild_feed(site_config: Any) -> None:
    """Rebuild podcast/feed.xml on R2 (once/cycle).

    Delegates to the shared ``services.media_feed_rebuild`` helper — the same
    seam ``media_approval_service.decide`` uses to rebuild on approval — so
    there's one rebuild implementation, not two copies.
    """
    from services.media_feed_rebuild import rebuild_podcast_feed

    await rebuild_podcast_feed(site_config)


class PodcastDistributeJob:
    name = "podcast_distribute"
    description = (
        "Link podcast_pipeline-rendered assets to their published post, seed "
        "Gate-2 podcast approvals (incl. backlog heal), and deliver approved "
        "episodes to R2 + rebuild the RSS feed (dormant until "
        "podcast_pipeline_trigger_enabled)"
    )
    schedule = "every 10 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        sc = config.get("_site_config")
        if sc is None:
            return JobResult(ok=True, detail="no site_config — skipping", changes_made=0)

        if not sc.get_bool("podcast_pipeline_trigger_enabled", False):
            return JobResult(
                ok=True,
                detail="podcast_pipeline_trigger_enabled=false — dormant",
                changes_made=0,
            )

        if pool is None:
            return JobResult(ok=True, detail="no pool — skipping", changes_made=0)

        limit = _max_per_cycle(sc)
        seeded = 0

        # Pass 1: link unlinked task-keyed assets + seed their approval.
        try:
            unlinked = await pool.fetch(_UNLINKED_PODCAST_SQL, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PODCAST_DISTRIBUTE] unlinked query failed: %s", exc)
            unlinked = []
        for row in unlinked or []:
            asset_id = row["id"]
            task_id = row.get("task_id")
            try:
                post_id = await pool.fetchval(_RESOLVE_POST_SQL, str(task_id))
                if not post_id:
                    continue  # not published yet — link on a later cycle

                # Link-time guard (#884): a post holds at most one podcast asset.
                # If it already has one, back-stamping this task-keyed render would
                # violate uniq_media_assets_post_podcast_type (and the orphan would
                # retry every cycle), so prune the redundant render instead.
                try:
                    already = await pool.fetchval(_EXISTING_PODCAST_SQL, post_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[PODCAST_DISTRIBUTE] existing-asset check failed for post %s: %s",
                        post_id, exc,
                    )
                    already = None
                if already:
                    try:
                        await pool.execute(_PRUNE_ORPHAN_SQL, asset_id)
                        logger.info(
                            "[PODCAST_DISTRIBUTE] pruned orphan render %s (task %s) — "
                            "post %s already has a podcast asset",
                            asset_id, task_id, post_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[PODCAST_DISTRIBUTE] orphan prune failed for asset %s: %s",
                            asset_id, exc,
                        )
                    emit_finding(
                        source="podcast_distribute",
                        kind="duplicate_podcast_asset",
                        severity="warn",
                        title=f"Redundant podcast render pruned for post {post_id}",
                        body=(
                            f"Post {post_id} already had a podcast media_asset; the "
                            f"duplicate task-keyed render {asset_id} (task {task_id}) "
                            f"was deleted to honor the one-podcast-per-post guard."
                        ),
                        dedup_key=f"duplicate_podcast_asset:{asset_id}",
                        extra={"post_id": str(post_id), "asset_id": str(asset_id)},
                    )
                    continue

                await pool.execute(_LINK_SQL, post_id, asset_id)
                await record_pending(
                    pool, post_id, "podcast",
                    file_path=row.get("storage_path") or None,
                    site_config=sc,
                )
                seeded += 1
                logger.info("[PODCAST_DISTRIBUTE] linked asset %s → post %s + seeded", asset_id, post_id)
            except Exception as exc:  # noqa: BLE001 — one asset must not halt the pass
                logger.warning("[PODCAST_DISTRIBUTE] link/seed failed for %s: %s", row.get("id"), exc)

        # Pass 2: heal the backlog — seed any linked-but-unapproved podcast asset.
        try:
            unapproved = await pool.fetch(_UNAPPROVED_LINKED_SQL, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PODCAST_DISTRIBUTE] backlog query failed: %s", exc)
            unapproved = []
        for row in unapproved or []:
            try:
                await record_pending(
                    pool, row["post_id"], "podcast",
                    file_path=row.get("storage_path") or None,
                    site_config=sc,
                )
                seeded += 1
                logger.info("[PODCAST_DISTRIBUTE] backlog-seeded podcast approval for post %s", row["post_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("[PODCAST_DISTRIBUTE] backlog seed failed for %s: %s", row.get("post_id"), exc)

        # Pass 3: deliver approved, undispatched assets → R2 + feed rebuild.
        dispatched = 0
        try:
            approved = await pool.fetch(_APPROVED_UNDISPATCHED_SQL, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PODCAST_DISTRIBUTE] approved query failed: %s", exc)
            approved = []
        for row in approved or []:
            if await _deliver_podcast(pool, sc, row):
                dispatched += 1
        if dispatched:
            await _rebuild_feed(sc)

        detail = f"seeded {seeded}, delivered {dispatched}"
        return JobResult(ok=True, detail=detail, changes_made=seeded + dispatched)


__all__ = ["PodcastDistributeJob"]
