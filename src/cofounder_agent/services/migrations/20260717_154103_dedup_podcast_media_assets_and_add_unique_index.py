"""Migration 20260717_154103_dedup_podcast_media_assets_and_add_unique_index: de-duplicate podcast media_assets rows and enforce one-per-post

ISSUE: Glad-Labs/poindexter#884

Background: ``record_media_asset`` only appended the ``(post_id, type)``
``ON CONFLICT`` upsert for the video family (``video`` / ``video_short``) —
``type='podcast'`` fell through to a plain ``INSERT``. So
``PodcastService.generate_episode(force=True)`` accumulated a fresh duplicate
row per regen, and the two render paths compound it: the Stage-3
``podcast.persist`` atom writes a **task-keyed** ``{task_id}.mp3`` row
(``post_id=NULL``, linked to the post later) while ``PodcastService`` writes a
**post-keyed** ``{post_id}.mp3`` row — cross-path duplicates that point at
different files. ``podcast_distribute`` then joined ``media_assets`` on
``(post_id, type='podcast')`` with no newest-row filter, so on Gate-2 approval
it could upload a non-deterministic (possibly stale) render to the deterministic
public key ``podcast/{cdn_ver}/{post_id}.mp3`` and publish it to the
Apple/Spotify feed.

The video lane already solved this with ``uniq_media_assets_post_video_type``;
this migration brings podcast to parity. In one transaction it:

  1. Backs up every redundant podcast row — all but the newest per ``post_id`` —
     into ``media_assets_dedup_backup`` (the same audit-trail table the #1460
     video de-dup used) so the delete is recoverable.
  2. Deletes those redundant rows. The retained newest row keeps the post's
     canonical episode; a de-linked older render's on-disk file is
     retention-managed separately (it is not deleted here).
  3. Creates the partial unique index ``uniq_media_assets_post_podcast_type``
     ``(post_id, type) WHERE post_id IS NOT NULL AND type='podcast'`` — the
     target ``record_media_asset``'s new ``_PODCAST_UPSERT_CONFLICT`` infers
     against, and the DB-level guard ``podcast_distribute``'s new link-time
     prune leans on (mirroring ``media_distribute``).

"Newest" is ``(created_at DESC, id DESC)`` — a stable tiebreak so a re-run is
deterministic — the same rule ``podcast_distribute._APPROVED_UNDISPATCHED_SQL``
now delivers on, so the row this migration keeps is exactly the row delivery
would pick.

Ordering matters: the delete MUST precede the index create, else the unique
index build fails on the existing duplicates. The three steps run in one
transaction so a mid-way failure leaves no partial de-dup (the runner records a
migration only after ``up()`` returns, so a failure is retried next boot).

Idempotent: after the de-dup no post has >1 in-scope podcast row, so a re-run
selects zero losers (no backup, no delete) and the index is
``CREATE ... IF NOT EXISTS``.

APPLY PATH: reaches prod when the worker next runs the runner at boot
(``StartupManager._run_migrations``). CI's ``migrations-smoke`` validates against
a throwaway DB where ``media_assets`` is empty — the de-dup selects nothing and
the index creates trivially, proving the migration is well-formed (not that it
ran against prod's actual duplicates).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Redundant podcast rows = every post-keyed podcast asset that is NOT the newest
# for its post. "Newest" = (created_at DESC, id DESC): the id tiebreak keeps the
# selection deterministic when two rows share a created_at, so backup + delete
# operate on an identical set and a re-run is stable. Only post-keyed podcast
# rows are in scope — task-keyed rows (post_id NULL) sit outside the partial
# index and are linked/de-duplicated at distribution time.
_SELECT_LOSER_IDS_SQL = """
    WITH ranked AS (
        SELECT id,
               row_number() OVER (
                   PARTITION BY post_id
                   ORDER BY created_at DESC, id DESC
               ) AS rn
          FROM media_assets
         WHERE type = 'podcast' AND post_id IS NOT NULL
    )
    SELECT id FROM ranked WHERE rn > 1
"""

# Copy the losers into the audit-trail backup table before deleting them, so the
# de-dup is recoverable (mirrors the #1460 video de-dup). Explicit column list —
# a schema drift on either table fails loud rather than silently mis-aligning
# columns. Plain INSERT (no ON CONFLICT): media_assets_dedup_backup has no unique
# constraint on id, and the migration runs once, so a conflict clause is neither
# needed nor inferrable.
_BACKUP_LOSERS_SQL = """
    INSERT INTO media_assets_dedup_backup (
        id, tenant_id, site_id, type, source, storage_provider, url,
        storage_path, thumbnail_url, title, description, alt_text, metadata,
        ai_metadata, task_id, created_at, updated_at, post_id, provider_plugin,
        width, height, duration_ms, file_size_bytes, mime_type, cost_usd,
        electricity_kwh, platform_video_ids
    )
    SELECT
        id, tenant_id, site_id, type, source, storage_provider, url,
        storage_path, thumbnail_url, title, description, alt_text, metadata,
        ai_metadata, task_id, created_at, updated_at, post_id, provider_plugin,
        width, height, duration_ms, file_size_bytes, mime_type, cost_usd,
        electricity_kwh, platform_video_ids
      FROM media_assets
     WHERE id = ANY($1::uuid[])
"""

_DELETE_LOSERS_SQL = "DELETE FROM media_assets WHERE id = ANY($1::uuid[])"

# Partial unique index — one podcast asset per post. Predicate MUST stay
# byte-identical to record_media_asset._PODCAST_UPSERT_CONFLICT so PG's conflict
# inference resolves the ON CONFLICT against it. Mirrors
# uniq_media_assets_post_video_type.
_CREATE_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uniq_media_assets_post_podcast_type "
    "ON public.media_assets USING btree (post_id, type) "
    "WHERE (post_id IS NOT NULL AND type = 'podcast')"
)


async def up(pool) -> None:
    """De-duplicate existing podcast rows (newest-per-post wins) and create the
    partial unique index that keeps them de-duplicated going forward.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            loser_ids = [r["id"] for r in await conn.fetch(_SELECT_LOSER_IDS_SQL)]
            if loser_ids:
                await conn.execute(_BACKUP_LOSERS_SQL, loser_ids)
                await conn.execute(_DELETE_LOSERS_SQL, loser_ids)
            await conn.execute(_CREATE_INDEX_SQL)
    logger.info(
        "Migration dedup_podcast_media_assets: de-duplicated %d redundant "
        "podcast row(s) (backed up to media_assets_dedup_backup) + created "
        "uniq_media_assets_post_podcast_type",
        len(loser_ids),
    )


async def down(pool) -> None:
    """Drop the unique index.

    One-way for the data: the de-duplicated rows are NOT restored from
    ``media_assets_dedup_backup``. Re-inserting them would reintroduce the exact
    duplicates this migration exists to remove; the backup table remains for
    manual recovery if ever needed.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS uniq_media_assets_post_podcast_type"
        )
