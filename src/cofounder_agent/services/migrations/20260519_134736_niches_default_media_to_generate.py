"""Add ``niches.default_media_to_generate`` + backfill ``posts.media_to_generate``.

ISSUE: Glad-Labs/glad-labs-stack#480 (per-niche job enable/disable).

Why: ``posts.media_to_generate`` is the canonical seam for "which derived
media (podcast / video / etc.) should this post spawn?" — but the
pipeline path (``publish_service.publish_post_from_task``) never
populated it, so every post got the column's default ``[]``. The
backfill jobs ``BackfillPodcastsJob`` + ``BackfillVideosJob`` then
ignored the field entirely and generated podcasts/videos for every
published post, including ``dev_diary`` posts where Matt explicitly
doesn't want them.

Earlier PR Glad-Labs/glad-labs-stack#481 plugged the dev_diary leak
with a slug-pattern filter — a hack Matt rightly rejected via Telegram
2026-05-19. The proper seam is per-niche config + populating the
field at insert time. This migration adds the config home and
backfills legacy rows so the seam works for existing data too.

Schema change:

- ``niches.default_media_to_generate text[]`` — array of media-type
  strings (``'podcast'``, ``'video'``, ``'video_short'``,
  ``'video_long'``) the niche wants spawned automatically from each
  published post. Empty array = no derived media for this niche.

Seeds:

- ``dev_diary``: ``ARRAY[]::text[]`` — dev diary posts are operational
  metadata, not narratable content.
- ``glad-labs``: ``ARRAY['podcast','video','video_short']`` — matches
  current effective behavior of the backfill jobs.

Legacy backfill:

- Existing ``posts.media_to_generate`` rows are all ``[]`` regardless
  of niche (the bug above). UPDATE them to match niche defaults:
  - dev_diary posts → ``ARRAY[]::text[]``
  - everything else → ``ARRAY['podcast','video','video_short']``
- Identifying dev_diary posts in legacy data: slug-suffix → first 8
  chars of pipeline_tasks.task_id join. Slug suffix is the disambiguator
  the publish path appends to every slug. This is one-shot archaeology,
  not ongoing filter logic — explicitly excluded from the
  ``feedback_filter_on_seams_not_slugs`` rule against production-code
  slug matching.
- Posts whose slug-suffix doesn't resolve to a pipeline_tasks row
  (oldest 7 of 66) fall through to the glad-labs default; if any of
  those was actually dev_diary, the next backfill run will simply not
  spawn new media for it, and the operator can flip its row by hand.

Going forward (in a follow-up code change in this same PR):
``publish_service.publish_post_from_task`` reads ``niche_slug`` from
the pipeline task, looks up the niche's ``default_media_to_generate``,
and stamps it on ``post_data`` before the INSERT. New posts then carry
the right value from the start.

Idempotent — the column add uses ``IF NOT EXISTS``, the seeds use
``ON CONFLICT`` semantics (no PK collision possible since we're
UPDATEing existing niche rows), and the legacy backfill is gated on
``media_to_generate = ARRAY[]::text[]`` so a replay doesn't clobber
operator-tuned values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Add column (idempotent — IF NOT EXISTS).
            await conn.execute(
                """
                ALTER TABLE niches
                ADD COLUMN IF NOT EXISTS default_media_to_generate text[]
                NOT NULL DEFAULT ARRAY[]::text[]
                """
            )

            # 2. Seed per-niche defaults. UPDATE (niche rows already exist).
            await conn.execute(
                """
                UPDATE niches
                   SET default_media_to_generate = ARRAY[]::text[]
                 WHERE slug = 'dev_diary'
                """
            )
            await conn.execute(
                """
                UPDATE niches
                   SET default_media_to_generate
                     = ARRAY['podcast','video','video_short']::text[]
                 WHERE slug = 'glad-labs'
                """
            )

            # 3. Backfill posts.media_to_generate using slug-suffix
            #    archaeology to derive niche for legacy rows. Only
            #    touch rows that are currently the all-empty default
            #    so operators can override per-post manually without
            #    a migration replay stomping the override.
            #
            # First pass: dev_diary posts.
            await conn.execute(
                """
                UPDATE posts p
                   SET media_to_generate = ARRAY[]::text[]
                  FROM pipeline_tasks pt
                 WHERE pt.task_id LIKE RIGHT(p.slug, 8) || '%'
                   AND pt.niche_slug = 'dev_diary'
                   AND p.status = 'published'
                   AND (p.media_to_generate IS NULL
                        OR p.media_to_generate = ARRAY[]::text[])
                """
            )

            # Second pass: every other published post defaults to
            # glad-labs media list. This covers both:
            #   - posts that joined to a pipeline_tasks row with
            #     niche_slug != 'dev_diary'
            #   - posts where the slug-suffix join didn't find a row
            #     (no pipeline_tasks survival — assume glad-labs)
            await conn.execute(
                """
                UPDATE posts
                   SET media_to_generate
                     = ARRAY['podcast','video','video_short']::text[]
                 WHERE status = 'published'
                   AND (media_to_generate IS NULL
                        OR media_to_generate = ARRAY[]::text[])
                   AND NOT EXISTS (
                       SELECT 1 FROM pipeline_tasks pt
                        WHERE pt.task_id LIKE RIGHT(posts.slug, 8) || '%'
                          AND pt.niche_slug = 'dev_diary'
                   )
                """
            )

    logger.info(
        "[migration] niches.default_media_to_generate column added + "
        "seeded; legacy posts.media_to_generate backfilled by niche."
    )
