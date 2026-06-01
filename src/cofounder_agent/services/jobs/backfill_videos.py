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
import re
from typing import Any

from plugins.job import JobResult

logger = logging.getLogger(__name__)

# External YouTube Data API v3 limits — NOT operator-tunable settings.
# The adapter (services/publish_adapters/youtube.py) enforces the hard
# caps; we build values that stay comfortably under them so the upload
# never 400s mid-stream.
#   - description ≤ 5000 chars (YouTube hard cap). We compose to ≤ 4800
#     to leave headroom for the back-link line / trailing whitespace.
#   - tags: ≤ 30 individual tags AND ≤ 500 chars when comma-joined.
_YOUTUBE_DESCRIPTION_BUDGET = 4800
_YOUTUBE_MAX_TAGS = 30
_YOUTUBE_TAGS_JOINED_LIMIT = 500


def _strip_markup(text: str) -> str:
    """Strip HTML/markdown tags and collapse whitespace.

    Mirrors the ``re.sub(r"<[^>]+>", "", ...)`` + whitespace-collapse
    approach used for the SEO excerpt in
    ``publish_service`` (~line 546) so the video description body reads
    as plain text rather than leaking ``<img>`` / ``<a>`` markup.
    """
    if not text:
        return ""
    stripped = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _parse_seo_keywords(seo_keywords: str) -> list[str]:
    """Parse the comma-separated ``posts.seo_keywords`` column into tags.

    Strips each keyword, drops empties, caps at 30 tags, and trims
    trailing tags until the comma-joined string fits YouTube's combined
    500-char tag limit. Returns ``[]`` when there are no usable keywords
    (caller converts that to ``tags=None``).
    """
    tags = [k.strip() for k in (seo_keywords or "").split(",") if k.strip()]
    tags = tags[:_YOUTUBE_MAX_TAGS]
    # Drop trailing tags until the joined string is under the limit.
    while tags and len(",".join(tags)) > _YOUTUBE_TAGS_JOINED_LIMIT:
        tags.pop()
    return tags


def _build_youtube_description(
    *,
    seo_description: str,
    body: str,
    site_config: Any,
    slug: str,
) -> str:
    """Compose the YouTube video description from SEO metadata + body.

    Layout::

        {seo_description}

        Read the full post: {site_url}/posts/{slug}

        {body_excerpt}

    ``seo_description`` comes from ``posts.excerpt`` (empty string when
    null). ``body_excerpt`` is the stripped content body, trimmed so the
    TOTAL composed description stays ≤ 4800 chars. The "Read the full
    post" line is omitted gracefully (logged at info) when ``site_url``
    can't be resolved or ``slug`` is missing — never raises.
    """
    seo_description = (seo_description or "").strip()

    # Resolve the canonical back-link. Missing site_url / slug → omit the
    # line (the only deliberate graceful fallback here, per the #275
    # design); log it so the operator knows why it's absent.
    backlink = ""
    site_url = ""
    if site_config is not None:
        try:
            site_url = str(site_config.require("site_url") or "").rstrip("/")
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "[BACKFILL_VIDEOS] site_url unavailable — omitting "
                "YouTube back-link: %s", exc,
            )
            site_url = ""
    if site_url and slug:
        backlink = f"Read the full post: {site_url}/posts/{slug}"
    elif not slug:
        logger.info(
            "[BACKFILL_VIDEOS] slug missing — omitting YouTube back-link",
        )

    body_excerpt = _strip_markup(body)

    # Compose with blank-line separators, skipping empty segments so a
    # null seo_description doesn't leave a leading blank line.
    header_parts = [p for p in (seo_description, backlink) if p]
    header = "\n\n".join(header_parts)

    if not header:
        # No SEO desc and no back-link — description is just the body.
        return body_excerpt[:_YOUTUBE_DESCRIPTION_BUDGET]

    if not body_excerpt:
        return header[:_YOUTUBE_DESCRIPTION_BUDGET]

    # Reserve room for the header + the "\n\n" joining it to the body,
    # then trim the body to fit the remaining budget.
    remaining = _YOUTUBE_DESCRIPTION_BUDGET - len(header) - 2
    if remaining <= 0:
        return header[:_YOUTUBE_DESCRIPTION_BUDGET]
    return f"{header}\n\n{body_excerpt[:remaining]}"


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

        # Iterate niche-by-niche so an operator can flip ``backfill_videos``
        # OFF for a single niche via one app_settings row
        # (``niche.<slug>.jobs.backfill_videos.enabled = false``) without
        # touching ``backfill_podcasts`` or any per-post ``media_to_generate``
        # array (Glad-Labs/poindexter#521). The global master switch
        # ``plugin.job.backfill_videos.enabled`` still gates the whole job
        # upstream in the scheduler.
        #
        # Filtering still rides the canonical seam, not slug patterns
        # (``feedback_filter_on_seams_not_slugs``): within an *enabled*
        # niche we select published posts whose ``media_to_generate``
        # overlaps both the video flavors AND that niche's
        # ``default_media_to_generate`` policy array. Posts pick up
        # ``media_to_generate`` at publish time from
        # ``niches.default_media_to_generate`` (see
        # ``publish_service.publish_post_from_task`` + migration
        # ``20260519_134736_niches_default_media_to_generate.py``).
        # ``excerpt`` carries the SEO meta description and ``seo_keywords``
        # is a comma-separated string column — both are threaded into the
        # YouTube payload (glad-labs-stack#275). ``slug`` builds the
        # canonical back-link.
        from services.jobs.niche_job_flags import niche_job_enabled
        from services.niche_service import NicheService

        _VIDEO_FLAVORS = ["video", "video_long", "video_short"]
        niches = await NicheService(pool).list_active()
        skipped_niches: list[str] = []

        cloud = await asyncpg.connect(cloud_url)
        try:
            posts: list[Any] = []
            seen_ids: set[str] = set()
            for niche in niches:
                if not niche_job_enabled(sc, niche.slug, self.name):
                    # Per-niche opt-out — short-circuit before any query.
                    skipped_niches.append(niche.slug)
                    logger.info(
                        "[BACKFILL_VIDEOS] niche %r disabled via "
                        "niche.%s.jobs.%s.enabled=false — skipping",
                        niche.slug, niche.slug, self.name,
                    )
                    continue

                niche_media = await cloud.fetchval(
                    "SELECT default_media_to_generate FROM niches WHERE id = $1",
                    niche.id,
                )
                if not niche_media or not (set(niche_media) & set(_VIDEO_FLAVORS)):
                    # This niche's policy doesn't opt into any video flavor.
                    continue

                niche_posts = await cloud.fetch(
                    """
                    SELECT id::text, title, content, excerpt, seo_keywords, slug
                    FROM posts
                    WHERE status = 'published'
                      AND media_to_generate && $1::text[]
                      AND media_to_generate && $2::text[]
                    ORDER BY published_at DESC LIMIT $3
                    """,
                    _VIDEO_FLAVORS,
                    list(niche_media),
                    post_limit,
                )
                for p in niche_posts:
                    if p["id"] not in seen_ids:
                        seen_ids.add(p["id"])
                        posts.append(p)
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
                    site_config=sc,
                    # SEO parity (#539) — reused from the posts row (already
                    # fetched for the YouTube payload), stamped into the
                    # video's media_assets row. No LLM regeneration.
                    seo_description=post["excerpt"] or "",
                    seo_keywords=post["seo_keywords"] or "",
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
                        from services import (
                            media_approval_service,
                            media_quality_service,
                        )
                        approval_conn = await asyncpg.connect(cloud_url)
                        try:
                            await media_approval_service.record_pending(
                                approval_conn, post_id, "video",
                            )
                            await media_quality_service.evaluate_video(
                                approval_conn, post_id, str(video_path),
                                medium="video",
                            )
                        finally:
                            await approval_conn.close()
                    except Exception as gate_err:
                        logger.warning(
                            "[BACKFILL_VIDEOS] media_approval / quality "
                            "eval failed for %s: %s", post_id[:8], gate_err,
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
                        seo_description=post["excerpt"] or "",
                        seo_keywords=post["seo_keywords"] or "",
                        slug=post["slug"] or "",
                    )
                if generated >= max_per_cycle:
                    break
            except Exception as e:
                logger.warning(
                    "[BACKFILL_VIDEOS] Failed for %s: %s",
                    post["title"][:30] if post.get("title") else post_id[:8], e,
                )

        detail = f"generated {generated} video(s)"
        if skipped_niches:
            detail += f", skipped_niches={','.join(skipped_niches)}"
        return JobResult(
            ok=True,
            detail=detail,
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
    seo_description: str = "",
    seo_keywords: str = "",
    slug: str = "",
) -> None:
    """Fire enabled video-platform adapters with the freshly-generated MP4.

    Reads ``publishing_adapters`` for rows in ``_VIDEO_PLATFORMS`` with
    ``enabled=true``, then calls the registry's ``dispatch`` for each.
    Per-adapter exceptions are isolated — one platform failing doesn't
    starve the others. Records ``last_run_*`` columns inline so the
    operator sees attempt history on the adapter row.

    Safe to call even when no adapters are configured — returns silently.

    **SEO payload (glad-labs-stack#275).** The YouTube payload is built
    from the post's structured SEO fields rather than the raw blog body:

    - ``description`` = ``{seo_description}`` (from ``posts.excerpt``) +
      a canonical "Read the full post: {site_url}/posts/{slug}" back-link
      + the markup-stripped body, composed to stay ≤ 4800 chars (under
      YouTube's 5000 cap). The back-link line is omitted gracefully (and
      logged) when ``site_url`` can't be resolved or ``slug`` is missing.
    - ``tags`` = the parsed ``posts.seo_keywords`` (comma-separated),
      stripped, capped at 30 tags and ≤ 500 joined chars; ``None`` when
      there are no keywords.

    ``seo_description`` / ``seo_keywords`` / ``slug`` are loaded from the
    ``posts`` row by the caller and threaded through here.
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

    # Build the SEO-rich description + tags from the post's structured
    # fields (glad-labs-stack#275). Both stay under YouTube's documented
    # caps; the adapter re-clamps as a backstop.
    description = _build_youtube_description(
        seo_description=seo_description,
        body=content,
        site_config=site_config,
        slug=slug,
    )
    tags = _parse_seo_keywords(seo_keywords)

    for row in rows:
        platform = row["platform"]
        payload = {
            "media_path": video_path,
            "title": title,
            "description": description,
            # Falsy tags → None; the handler treats None as "no tags".
            "tags": tags or None,
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
