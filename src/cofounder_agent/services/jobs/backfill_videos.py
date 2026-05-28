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
            # Filter on the canonical seam: only spawn videos for posts
            # whose niche policy opted in to at least one video flavor.
            # ``posts.media_to_generate`` is populated at publish time
            # from ``niches.default_media_to_generate`` (see
            # ``publish_service.publish_post_from_task`` and migration
            # ``20260519_134736_niches_default_media_to_generate.py``).
            # The ``&&`` (array overlap) operator matches a post that
            # opted in to any of the video media flavors. Anti-pattern
            # called out in ``feedback_filter_on_seams_not_slugs`` —
            # slug-pattern exclusions in this query were the hack Matt
            # rejected 2026-05-19.
            posts = await cloud.fetch(
                """
                SELECT id::text, title, content
                FROM posts
                WHERE status = 'published'
                  AND media_to_generate && ARRAY['video','video_long','video_short']::text[]
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
                    # Insert the awaiting-approval gate row so the
                    # YouTube publish adapter won't upload the video
                    # until the operator decides. Per-niche auto-approve
                    # (``niche.<slug>.media.video.auto_approve = true``)
                    # auto-resolves the row to ``approved`` so the
                    # downstream dispatch fires immediately on niches
                    # the operator trusts.
                    try:
                        from services import media_approval_service
                        approval_conn = await asyncpg.connect(cloud_url)
                        try:
                            await media_approval_service.record_pending(
                                approval_conn, post_id, "video",
                            )
                        finally:
                            await approval_conn.close()
                    except Exception as gate_err:
                        logger.warning(
                            "[BACKFILL_VIDEOS] media_approval insert "
                            "failed for %s: %s", post_id[:8], gate_err,
                        )
                    # After a successful local generation, dispatch to
                    # any enabled ``publishing_adapters`` rows whose
                    # platform is one of the video destinations
                    # (currently just youtube). The dispatcher checks
                    # ``media_approvals.status='approved'`` before
                    # firing each adapter — un-approved videos sit on
                    # disk waiting for operator decision.
                    await _dispatch_video_publishers(
                        pool=pool,
                        site_config=sc,
                        post_id=post_id,
                        video_path=str(video_path),
                        title=post["title"],
                        content=post["content"] or "",
                    )
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


# Set of platforms this job is responsible for distributing video to.
# Adding a new platform = add to this set + register an adapter row +
# write a ``publishing_<platform>.py`` handler shim. Three-step contract.
_VIDEO_PLATFORMS: frozenset[str] = frozenset({"youtube"})


async def _dispatch_video_publishers(
    *,
    pool: Any,
    site_config: Any,
    post_id: str,
    video_path: str,
    title: str,
    content: str,
) -> None:
    """Fire enabled video-platform adapters with the freshly-generated MP4.

    Reads ``publishing_adapters`` for rows in ``_VIDEO_PLATFORMS`` with
    ``enabled=true``, then calls the registry's ``dispatch`` for each.
    Per-adapter exceptions are isolated — one platform failing doesn't
    starve the others. Records ``last_run_*`` columns inline so the
    operator sees attempt history on the adapter row.

    Safe to call even when no adapters are configured — returns silently.
    """
    if pool is None:
        logger.debug("[BACKFILL_VIDEOS] no pool — skipping platform dispatch")
        return
    try:
        import asyncpg  # noqa: F401  (caller already imported it)

        from services.integrations import registry
        from services.integrations.handlers import load_all
        load_all()  # idempotent — ensures publishing_youtube is registered
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKFILL_VIDEOS] handler load failed (skipping platform "
            "dispatch): %s", exc,
        )
        return

    rows = []
    try:
        if hasattr(pool, "acquire"):
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT name, platform, handler_name, config, metadata
                      FROM publishing_adapters
                     WHERE enabled = true
                       AND platform = ANY($1::text[])
                    """,
                    list(_VIDEO_PLATFORMS),
                )
        else:
            rows = await pool.fetch(
                """
                SELECT name, platform, handler_name, config, metadata
                  FROM publishing_adapters
                 WHERE enabled = true
                   AND platform = ANY($1::text[])
                """,
                list(_VIDEO_PLATFORMS),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKFILL_VIDEOS] publishing_adapters lookup failed "
            "(skipping platform dispatch): %s", exc,
        )
        return

    if not rows:
        logger.debug(
            "[BACKFILL_VIDEOS] no enabled video adapters in "
            "publishing_adapters — skipping",
        )
        return

    # Per-medium operator approval gate. The media file is generated
    # locally but won't reach the external surface (YouTube, etc.)
    # until the operator approves — or until per-niche auto-approve
    # was set, in which case ``record_pending`` already wrote the row
    # as ``approved`` above. Missing row = not approved (conservative
    # default per ``feedback_no_silent_defaults``).
    try:
        from services import media_approval_service
        if hasattr(pool, "acquire"):
            async with pool.acquire() as conn:
                approved = await media_approval_service.is_approved(
                    conn, post_id, "video",
                )
        else:
            approved = await media_approval_service.is_approved(
                pool, post_id, "video",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKFILL_VIDEOS] media_approval lookup failed for %s "
            "(treating as NOT approved — operator must re-decide): %s",
            post_id, exc,
        )
        return
    if not approved:
        logger.info(
            "[BACKFILL_VIDEOS] video for %s awaiting operator approval "
            "— skipping platform dispatch", post_id,
        )
        return

    # Truncate content to a reasonable video-description length here so
    # every adapter receives the same trimmed body. Adapter-specific
    # caps (YouTube=5000) apply on top.
    description = (content or "").strip()[:4000]

    for row in rows:
        platform = row["platform"]
        payload = {
            "media_path": video_path,
            "title": title,
            "description": description,
            "post_id": post_id,
        }
        try:
            result = await registry.dispatch(
                "publishing",
                row["handler_name"] or platform,
                payload,
                site_config=site_config,
                row=dict(row),
                pool=pool,
            )
            success = bool(result.get("success")) if isinstance(result, dict) else False
            if success:
                logger.info(
                    "[BACKFILL_VIDEOS] %s upload succeeded for post %s",
                    platform, post_id,
                )
            else:
                logger.warning(
                    "[BACKFILL_VIDEOS] %s upload returned failure for "
                    "post %s: %s",
                    platform, post_id,
                    (result or {}).get("error") if isinstance(result, dict) else result,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[BACKFILL_VIDEOS] %s upload raised for post %s: %s",
                platform, post_id, exc,
            )
