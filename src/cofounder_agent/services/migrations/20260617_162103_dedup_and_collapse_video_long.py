"""Migration 20260617_162103: de-dup video assets + collapse video_long -> video.

ISSUE: Glad-Labs/poindexter#689   (video-side cutover, glad-labs-stack#1460)

The video-side cutover. media_assets had no (post_id, type) uniqueness, so two
producers (pipeline ``video_long`` + reconciliation ``video``) and earlier dup
reconciliation stamps left several posts with multiple video-family rows.
Collapsing the names without de-dup first would let media_distribute double-upload
to YouTube. In one transaction this migration:

  1. Backs up every row it will delete into media_assets_dedup_backup (recovery).
  2. Per post, keeps ONE survivor per family — long = {video, video_long},
     short = {video_short} — by smart priority: has a platform video id >
     source='pipeline' > newest (created_at, then id). Deletes the losers.
  3. Relabels each surviving 'video_long' -> 'video'.
  4. Creates the partial unique guard uniq_media_assets_post_video_type so dup
     video-family rows can never recur (the root cause).

FK-safe: nothing references media_assets.id. Idempotent + light-env safe — on a
fresh baseline DB there are no video rows, so steps 1-3 are no-ops and only the
index is created (over an empty set).

The de-dup priority expression mirrors media_distribute._APPROVED_UNDISPATCHED_SQL
and the recorder's ON CONFLICT predicate is held identical to _UNIQUE_INDEX, so
all three agree on "one canonical video per post".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Archive table for the losers — same columns as media_assets (no constraints/
# indexes needed; it's a cold recovery copy). LIKE is safe: media_assets has no
# generated columns, so INSERT ... SELECT * aligns.
_DEDUP_BACKUP_DDL = "CREATE TABLE IF NOT EXISTS media_assets_dedup_backup (LIKE media_assets)"

# Rank the video-family rows per (post, family) and collect everyone but the
# winner. platform_video_ids is NOT NULL DEFAULT '{}', so the real discriminator
# is "non-empty json" (a real platform id beats a default '{}').
_DEDUP_LOSERS_TEMP = """
CREATE TEMP TABLE _video_dedup_losers ON COMMIT DROP AS
SELECT id FROM (
    SELECT id,
           row_number() OVER (
               PARTITION BY post_id,
                            CASE WHEN type IN ('video', 'video_long') THEN 'long' ELSE 'short' END
               ORDER BY
                   (platform_video_ids IS NOT NULL
                    AND platform_video_ids::text NOT IN ('', 'null', '{}')) DESC,
                   (source = 'pipeline') DESC,
                   created_at DESC NULLS LAST,
                   id DESC
           ) AS rn
      FROM media_assets
     WHERE post_id IS NOT NULL
       AND type IN ('video', 'video_long', 'video_short')
) ranked
WHERE rn > 1
"""

_DEDUP_ARCHIVE = """
INSERT INTO media_assets_dedup_backup
SELECT * FROM media_assets WHERE id IN (SELECT id FROM _video_dedup_losers)
"""

_DEDUP_DELETE = "DELETE FROM media_assets WHERE id IN (SELECT id FROM _video_dedup_losers)"

_RELABEL = "UPDATE media_assets SET type = 'video', updated_at = NOW() WHERE type = 'video_long'"

# Root-cause guard: at most one 'video' + one 'video_short' per post. Partial so
# task-keyed renders (post_id IS NULL, media.persist at Stage-2) and non-video
# families are unconstrained. The predicate MUST match media_asset_recorder's
# ON CONFLICT clause for conflict inference to resolve.
_UNIQUE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uniq_media_assets_post_video_type
    ON media_assets (post_id, type)
 WHERE post_id IS NOT NULL AND type IN ('video', 'video_short')
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DEDUP_BACKUP_DDL)
            await conn.execute(_DEDUP_LOSERS_TEMP)
            archived = await conn.execute(_DEDUP_ARCHIVE)
            deleted = await conn.execute(_DEDUP_DELETE)
            relabeled = await conn.execute(_RELABEL)
            await conn.execute(_UNIQUE_INDEX)
    logger.info(
        "Migration 20260617_162103: dedup_and_collapse_video_long — "
        "archived=%s deleted=%s relabeled=%s; uniq_media_assets_post_video_type ensured",
        archived, deleted, relabeled,
    )


async def down(pool) -> None:
    """Drop only the unique guard. The data de-dup is intentionally NOT auto-
    reversed — recover deleted rows from media_assets_dedup_backup if needed."""
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS uniq_media_assets_post_video_type")
    logger.info("Migration 20260617_162103: dropped uniq_media_assets_post_video_type")
