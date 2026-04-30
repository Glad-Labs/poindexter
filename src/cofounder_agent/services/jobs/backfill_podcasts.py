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
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class BackfillPodcastsJob:
    name = "backfill_podcasts"
    description = "Generate + R2-sync podcast episodes for published posts"
    schedule = "every 4 hours"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        from services.site_config import site_config

        cloud_url = site_config.get("database_url", "")
        if not cloud_url:
            return JobResult(ok=True, detail="no database_url — skipping", changes_made=0)

        try:
            import asyncpg
        except ImportError:
            return JobResult(ok=False, detail="asyncpg not available", changes_made=0)

        from services.podcast_service import PodcastService

        post_limit = int(config.get("post_limit", 20))
        max_per_cycle = int(config.get("max_per_cycle", 2))
        r2_sync_cap = int(config.get("r2_sync_cap", 5))

        cloud = await asyncpg.connect(cloud_url)
        try:
            posts = await cloud.fetch(
                """
                SELECT id::text, title, content
                FROM posts WHERE status = 'published'
                ORDER BY published_at DESC LIMIT $1
                """,
                post_limit,
            )
        finally:
            await cloud.close()

        svc = PodcastService()
        generated = 0
        uploaded = 0

        # Pass 1: sync existing local episodes to R2.
        try:
            from services.r2_upload_service import upload_podcast_episode
            sync_count = 0
            for post in posts:
                if svc.episode_exists(post["id"]) and sync_count < r2_sync_cap:
                    try:
                        r2_url = await upload_podcast_episode(post["id"])
                        if r2_url:
                            sync_count += 1
                    except Exception:  # noqa: BLE001 — sync failure shouldn't block generation
                        pass
            if sync_count > 0:
                uploaded += sync_count
                logger.info("[BACKFILL_PODCASTS] Synced %d episodes to R2", sync_count)
        except ImportError:
            # r2_upload_service missing → R2 offline, keep going on local-only mode.
            pass

        # Pass 2: generate new episodes.
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
                    # Upload the fresh episode to R2 too.
                    try:
                        from services.r2_upload_service import upload_podcast_episode
                        r2_url = await upload_podcast_episode(post["id"])
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

                from services.r2_upload_service import upload_to_r2
                from services.bootstrap_defaults import DEFAULT_WORKER_API_URL
                api_base = site_config.get("internal_api_base_url", DEFAULT_WORKER_API_URL)
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                    feed = await client.get(f"{api_base}/api/podcast/feed.xml", timeout=30)
                    feed_path = os.path.join(
                        os.path.expanduser("~"), ".poindexter", "podcast-feed.xml",
                    )
                    os.makedirs(os.path.dirname(feed_path), exist_ok=True)
                    with open(feed_path, "w", encoding="utf-8") as f:
                        f.write(feed.text)
                    await upload_to_r2(feed_path, "podcast/feed.xml", "application/rss+xml")
                    logger.info("[BACKFILL_PODCASTS] Podcast RSS feed rebuilt on R2")
            except Exception as feed_err:
                logger.warning(
                    "[BACKFILL_PODCASTS] Feed rebuild failed (non-fatal): %s", feed_err,
                )

        return JobResult(
            ok=True,
            detail=f"generated {generated}, uploaded {uploaded}",
            changes_made=generated + uploaded,
        )
