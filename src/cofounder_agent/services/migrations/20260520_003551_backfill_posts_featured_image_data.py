"""Backfill ``posts.featured_image_data`` from ``media_assets.metadata``.

ISSUE: Glad-Labs/glad-labs-stack 2026-05-19 jank-audit — featured_image_data
dead-seam finding.

Why: ``posts.featured_image_data`` is a ``jsonb`` column on ``posts``
(default ``{}``) that has existed since the baseline but was never
written by any production code path. The column was added to hold
the SDXL reproducibility payload (model name, prompt, seed, dimensions,
generation time) so an operator could later regenerate a similar
image OR debug a bad render — but the writer side
(``source_featured_image`` → ``publish_service`` → ``content_db.create_post``)
never persisted it.

Same dead-seam class as ``posts.media_to_generate`` before #482 and
``posts.word_count`` / ``posts.reading_time`` before the 2026-05-19
``20260519_191744_backfill_posts_word_count_reading_time.py`` migration:
the column existed, looked editable from the operator's perspective,
but nothing populated it.

The companion code change in this PR adds ``featured_image_data`` to
``content_db.create_post``'s INSERT, threads it through
``publish_service.publish_post_from_task``, and has
``source_featured_image`` populate the dict (with SDXL prompt / model /
seed / negative_prompt / generation_seconds for the SDXL branch, basic
provenance for the Pexels branch). This migration fixes the legacy
rows where we can — ``media_assets.metadata`` already captures most
of the same provenance (topic, task_id, photographer, image_style)
for posts published since GH#161 (media_assets producer hook). Pre-#161
posts and posts with no matching ``media_assets`` row stay at ``'{}'``
because the metadata isn't recoverable.

Strategy:

1. Find each ``posts`` row whose ``featured_image_data`` is ``'{}'``
   (the column default, i.e. never written) AND that has a matching
   ``media_assets`` row of ``type = 'featured_image'``.
2. Copy ``media_assets.metadata`` plus the asset's ``provider_plugin``,
   ``width``, and ``height`` onto ``posts.featured_image_data`` as a
   merged JSONB blob. Source field maps:

       media_assets.metadata.topic         → featured_image_data.topic
       media_assets.metadata.task_id       → featured_image_data.task_id
       media_assets.metadata.photographer  → featured_image_data.photographer
       media_assets.metadata.image_style   → featured_image_data.image_style
       media_assets.metadata.sdxl_model    → featured_image_data.sdxl_model
       media_assets.metadata.sdxl_seed     → featured_image_data.sdxl_seed
       media_assets.metadata.sdxl_prompt   → featured_image_data.sdxl_prompt
       media_assets.metadata.sdxl_negative_prompt
                                           → featured_image_data.sdxl_negative_prompt
       media_assets.metadata.sdxl_generation_time_ms
                                           → featured_image_data.generation_seconds (/1000)
       media_assets.provider_plugin        → featured_image_data.provider_plugin
       media_assets.width                  → featured_image_data.width
       media_assets.height                 → featured_image_data.height
       (derived from provider_plugin)      → featured_image_data.source
       media_assets.created_at             → featured_image_data.generated_at

3. Posts with no matching ``media_assets`` row (pre-#161) stay at
   ``'{}'`` — backfill from URL pattern isn't trustworthy enough.

Idempotent — the UPDATE filters on ``featured_image_data = '{}'`` so a
post that has already been backfilled (or hand-edited) is never
stomped on replay. Multiple matching ``media_assets`` rows are handled
by taking the most-recent one (``ORDER BY created_at DESC LIMIT 1``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# DDL kept as one statement so the entire backfill is a single
# atomic write — partial backfill on transaction abort would be
# surprising for operator-side queries.
_BACKFILL_SQL = """
UPDATE posts p
   SET featured_image_data = jsonb_strip_nulls(
        jsonb_build_object(
            'source',          COALESCE(
                                    ma.metadata->>'source',
                                    CASE
                                        WHEN ma.provider_plugin LIKE 'image.sdxl%%'
                                            THEN substring(ma.provider_plugin from 'image\\.(.*)')
                                        WHEN ma.provider_plugin = 'image.pexels'
                                            THEN 'pexels'
                                        ELSE NULL
                                    END
                                ),
            'provider_plugin', NULLIF(ma.provider_plugin, ''),
            'width',           ma.width,
            'height',          ma.height,
            'photographer',    ma.metadata->>'photographer',
            'topic',           ma.metadata->>'topic',
            'task_id',         ma.metadata->>'task_id',
            'image_style',     ma.metadata->>'image_style',
            'sdxl_model',      ma.metadata->>'sdxl_model',
            'sdxl_seed',       (ma.metadata->>'sdxl_seed')::int,
            'sdxl_prompt',     ma.metadata->>'sdxl_prompt',
            'sdxl_negative_prompt',
                               ma.metadata->>'sdxl_negative_prompt',
            'sdxl_filename',   ma.metadata->>'sdxl_filename',
            'generation_seconds',
                               CASE
                                   WHEN ma.metadata ? 'sdxl_generation_time_ms'
                                        THEN round(
                                                ((ma.metadata->>'sdxl_generation_time_ms')::numeric / 1000.0)::numeric,
                                                3
                                             )
                                   ELSE NULL
                               END,
            'generated_at',    to_char(
                                    ma.created_at AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                                ),
            'backfilled_from', 'media_assets'
        )
    )
  FROM (
        SELECT DISTINCT ON (post_id)
               post_id,
               metadata,
               provider_plugin,
               width,
               height,
               created_at
          FROM media_assets
         WHERE type = 'featured_image'
           AND post_id IS NOT NULL
         ORDER BY post_id, created_at DESC
  ) ma
 WHERE p.id = ma.post_id
   AND (p.featured_image_data IS NULL OR p.featured_image_data = '{}'::jsonb)
"""

# Use the same query against a temp CTE to count rows without
# performing the UPDATE — keeps the dry-run logic simple and avoids
# running the JSONB build twice for the same row in production.
_COUNT_ELIGIBLE_SQL = """
SELECT COUNT(*) AS n
  FROM posts p
  JOIN (
        SELECT DISTINCT ON (post_id) post_id
          FROM media_assets
         WHERE type = 'featured_image' AND post_id IS NOT NULL
         ORDER BY post_id, created_at DESC
  ) ma ON ma.post_id = p.id
 WHERE (p.featured_image_data IS NULL OR p.featured_image_data = '{}'::jsonb)
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            eligible = await conn.fetchval(_COUNT_ELIGIBLE_SQL)
            result = await conn.execute(_BACKFILL_SQL)

    logger.info(
        "[migration] posts.featured_image_data backfill: eligible=%s result=%s",
        eligible, result,
    )
