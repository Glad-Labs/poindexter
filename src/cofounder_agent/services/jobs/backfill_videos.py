"""BackfillVideosJob — generate videos for posts that have podcasts but no video.

Replaces ``IdleWorker._backfill_videos``. Runs every 6 hours by default
(matches the pre-refactor ``_is_due("video_backfill", 360)``).

GPU-heavy so we cap generation at 1 per cycle. The Job still runs
regardless of pipeline activity — ``plugin.job.backfill_videos.config.max_per_cycle``
lets operators tune it if the GPU has bandwidth.

Config (``plugin.job.backfill_videos``):
- ``enabled`` (default ``true``)
- ``interval_seconds`` (default 21600)
- ``config.post_limit`` (default 20) — how far back to look for candidates
- ``config.max_per_cycle`` (default 1) — GPU-bound cap per run
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)


class BackfillVideosJob:
    name = "backfill_videos"
    description = "Generate videos for published posts that have podcasts but no video"
    schedule = "every 6 hours"
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

        from services.podcast_service import PODCAST_DIR
        from services.video_service import VIDEO_DIR, generate_video_for_post

        post_limit = int(config.get("post_limit", 20))
        max_per_cycle = int(config.get("max_per_cycle", 1))

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

        generated = 0
        for post in posts:
            post_id = post["id"]
            podcast_path = PODCAST_DIR / f"{post_id}.mp3"
            video_path = VIDEO_DIR / f"{post_id}.mp4"

            # Only generate video if podcast exists but video doesn't.
            if not podcast_path.exists() or video_path.exists():
                continue

            try:
                result = await generate_video_for_post(
                    post_id=post_id,
                    title=post["title"],
                    content=post["content"] or "",
                )
                if result.success:
                    generated += 1
                    logger.info("[BACKFILL_VIDEOS] Generated video for: %s", post["title"][:40])
                if generated >= max_per_cycle:
                    break
            except Exception as e:
                logger.warning(
                    "[BACKFILL_VIDEOS] Failed for %s: %s",
                    post["title"][:30] if post.get("title") else post_id[:8], e,
                )

        return JobResult(
            ok=True,
            detail=f"generated {generated} video(s)",
            changes_made=generated,
        )
