"""BackfillPodcastsJob — generate + upload podcast episodes for published posts.

Replaces ``IdleWorker._backfill_podcasts``. Runs every 4 hours by
default. GPU-heavy (TTS generation), so max_per_cycle defaults to 2.

Two-pass work:
1. Sync any local-only episodes up to R2 (recovers from missed uploads).
2. Generate new episodes for published posts that don't have them yet.

If anything was uploaded, rebuild the podcast RSS feed on R2 so the
public feed stays fresh.

Config (``plugin.job.backfill_podcasts``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 14400)
- ``config.post_limit`` (default 20) — how far back to look
- ``config.max_per_cycle`` (default 2) — generation cap
- ``config.r2_sync_cap`` (default 5) — how many existing-but-unsynced
  episodes to push in the first pass
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class BackfillPodcastsJob:
    name = "backfill_podcasts"
    description = "Generate + R2-sync podcast episodes for published posts"
    schedule = "every 4 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330)
        sc = config.get("_site_config")
        cloud_url = sc.get("database_url", "") if sc is not None else ""
        if not cloud_url:
            return JobResult(ok=True, detail="no database_url — skipping", changes_made=0)

        try:
            import asyncpg
        except ImportError:
            return JobResult(ok=False, detail="asyncpg not available", changes_made=0)

        from services.podcast_service import PODCAST_DIR, PodcastService

        post_limit = int(config.get("post_limit", 20))
        max_per_cycle = int(config.get("max_per_cycle", 2))
        r2_sync_cap = int(config.get("r2_sync_cap", 5))

        cloud = await asyncpg.connect(cloud_url)
        try:
            # Filter on the canonical seam: only spawn podcasts for
            # posts whose niche policy opted in. ``posts.media_to_generate``
            # is populated at publish time from
            # ``niches.default_media_to_generate`` (see
            # ``publish_service.publish_post_from_task`` and migration
            # ``20260519_134736_niches_default_media_to_generate.py``).
            # Operators flip a niche on/off for podcasts by updating that
            # one array, no code change needed. Anti-pattern called out in
            # ``feedback_filter_on_seams_not_slugs`` — slug-pattern
            # exclusions in this query were the hack Matt rejected
            # 2026-05-19.
            posts = await cloud.fetch(
                """
                SELECT id::text, title, content
                FROM posts
                WHERE status = 'published'
                  AND 'podcast' = ANY(media_to_generate)
                ORDER BY published_at DESC LIMIT $1
                """,
                post_limit,
            )
        finally:
            await cloud.close()

        # ``sc`` is guaranteed non-None here: the ``not cloud_url`` early
        # return above bails when ``config['_site_config']`` is missing.
        # #272 Phase-2f made the ctor site_config mandatory.
        svc = PodcastService(site_config=sc)
        generated = 0
        uploaded = 0

        # Pass 1: sync existing local episodes to R2.
        try:
            from services.r2_upload_service import R2UploadService
            r2_svc = R2UploadService(site_config=sc) if sc is not None else None
            sync_count = 0
            if r2_svc is not None:
                for post in posts:
                    if svc.episode_exists(post["id"]) and sync_count < r2_sync_cap:
                        try:
                            r2_url = await r2_svc.upload_podcast_episode(post["id"])
                            if r2_url:
                                sync_count += 1
                        except Exception:  # noqa: BLE001 — sync failure shouldn't block generation
                            pass
            if sync_count > 0:
                uploaded += sync_count
                logger.info("[BACKFILL_PODCASTS] Synced %d episodes to R2", sync_count)
        except ImportError:
            # r2_upload_service missing → R2 offline, keep going on local-only mode.
            r2_svc = None

        # Pass 2: generate new episodes.
        # Re-open the cloud connection — it was closed at line 81 after
        # the posts query so we don't sit on it during long generation.
        # The approval-gate insert needs a connection at the END of each
        # successful generation, so we acquire fresh per-episode and
        # release immediately. This is a 4-hour-cadence backfill — the
        # extra connect/disconnect cost is irrelevant compared to the
        # ~30s per-episode generation latency.
        from services import media_approval_service

        for post in posts:
            if svc.episode_exists(post["id"]):
                continue
            try:
                result = await svc.generate_episode(
                    post_id=post["id"],
                    title=post["title"],
                    content=post["content"] or "",
                )
                if result.success:
                    generated += 1
                    logger.info(
                        "[BACKFILL_PODCASTS] Generated podcast for: %s",
                        post["title"][:40],
                    )
                    # Record the medium as awaiting approval, then run
                    # the Layer 1 quality eval. Both share a single
                    # connection — record_pending must land before eval
                    # so the eval's UPDATE has a row to write to.
                    # Failures here MUST NOT block the R2 upload below
                    # (the upload is durable storage of the file itself
                    # — the gate just controls whether the file enters
                    # the public feed).
                    try:
                        from services import media_quality_service
                        approval_conn = await asyncpg.connect(cloud_url)
                        try:
                            await media_approval_service.record_pending(
                                approval_conn, post["id"], "podcast",
                            )
                            await media_quality_service.evaluate_podcast(
                                approval_conn,
                                post["id"],
                                str(PODCAST_DIR / f"{post['id']}.mp3"),
                            )
                        finally:
                            await approval_conn.close()
                    except Exception as gate_err:
                        logger.warning(
                            "[BACKFILL_PODCASTS] media_approval / "
                            "quality eval failed for %s (file generated, "
                            "gate row may be missing — re-run backfill "
                            "to retry): %s",
                            post["id"][:8], gate_err,
                        )
                    # Upload the fresh episode to R2 too.
                    try:
                        if r2_svc is None:
                            from services.r2_upload_service import R2UploadService
                            r2_svc = (
                                R2UploadService(site_config=sc)
                                if sc is not None else None
                            )
                        if r2_svc is not None:
                            r2_url = await r2_svc.upload_podcast_episode(post["id"])
                            if r2_url:
                                uploaded += 1
                    except Exception as r2_err:
                        logger.warning(
                            "[BACKFILL_PODCASTS] R2 upload failed for %s: %s",
                            post["id"][:8], r2_err,
                        )
                if generated >= max_per_cycle:
                    break
            except Exception as e:
                logger.warning(
                    "[BACKFILL_PODCASTS] Generation failed for %s: %s",
                    post["title"][:30] if post.get("title") else post["id"][:8], e,
                )

        # Pass 3: rebuild the RSS feed on R2 if anything was uploaded.
        if uploaded > 0:
            try:
                import httpx

                from services.r2_upload_service import R2UploadService
                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                api_base = (
                    sc.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
                    if sc is not None else DEFAULT_WORKER_API_URL
                )
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                    feed = await client.get(f"{api_base}/api/podcast/feed.xml", timeout=30)
                    # Per-call temp file (matches publish_service) — the
                    # container's home dir (~/.poindexter) is not writable
                    # by appuser, which silently killed every feed rebuild.
                    fd, feed_path = tempfile.mkstemp(
                        suffix=".xml", prefix="poindexter-podcast-",
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.write(feed.text)
                        if sc is not None:
                            r2_svc = R2UploadService(site_config=sc)
                            await r2_svc.upload_to_r2(
                                feed_path, "podcast/feed.xml", "application/rss+xml",
                            )
                            logger.info("[BACKFILL_PODCASTS] Podcast RSS feed rebuilt on R2")
                    finally:
                        try:
                            os.unlink(feed_path)
                        except OSError:
                            pass
            except Exception as feed_err:
                logger.warning(
                    "[BACKFILL_PODCASTS] Feed rebuild failed (non-fatal): %s", feed_err,
                )

        return JobResult(
            ok=True,
            detail=f"generated {generated}, uploaded {uploaded}",
            changes_made=generated + uploaded,
        )
