"""Add ``pipeline_distributions.medium`` and re-key the table on it.

ISSUE: Glad-Labs/poindexter#1037

``pipeline_distributions`` was keyed ``UNIQUE (task_id, target)``, which encodes
"one task delivers one artifact per platform". That has been false since the
video lane started producing two renders: a post yields BOTH a long-form
``video`` and a ``video_short``, and ``media_distribute`` dispatches both to
``target='youtube'`` under the SAME ``task_id``. The second upsert therefore
overwrote the first, and one of the two YouTube handles was silently lost.

Five of the twelve YouTube rows on prod were collisions, and the forensics are
unambiguous — in every case the Short inserted first and the long form clobbered
it seconds-to-hours later, leaving ``created_at`` (the Short's insert) and
``published_at`` (the long form's update) on the same row disagreeing about which
upload it describes:

    task 4a4b9054  short fBk9Jdhln9w  lost to  long 5S-L5CB-ALg
    task 9318d724  short fdtFkrRITE0  lost to  long qHV5EoHFHlk
    task 1d10e119  short 2czD8xK9Ezk  lost to  long gYlAYDjVkC0
    task 68252b36  short weXBTm0yjgE  lost to  long v7bY2pKVeZQ
    task e311bcc1  short dZxk7FuodZo  lost to  long x65Y31Ul36w

Nothing was unrecoverable — ``media_assets.platform_video_ids`` keeps one
``{"youtube": "<id>"}`` per asset row and never collided — but
``services/youtube_metadata_sync.py`` reads ``pipeline_distributions``, so the
orphaned Shorts were unreachable by ``poindexter integrations youtube
sync-metadata``. They kept their pre-#3517 4,800-char description and never got
the ``#Shorts`` title suffix from #3518, leaving each one byte-identically
titled to its long-form twin on the channel.

**Why ``medium`` and not ``(task_id, target, external_id)``.** ``external_id`` is
nullable — every ``target='gladlabs.io'`` row has it NULL, and NULLs are distinct
in a unique index, so that key would let the blog-publish upsert insert an
unbounded pile of duplicate rows instead of updating in place. It would also
break the far more important case: a genuine RE-dispatch of the same render mints
a NEW video id, so it would append a second row rather than updating the one it
supersedes. ``medium`` names *which render this is*, which is stable across
re-dispatch, so the upsert keeps its "same render updates in place" contract.

``medium`` reuses the ``media_approvals.medium`` vocabulary (``video`` /
``video_short`` / ``podcast``) so the dispatcher can pass through the value it
already holds. ``'default'`` is the sentinel for a target with exactly one
undifferentiated artifact — every ``gladlabs.io`` row, and any future target that
delivers the post itself rather than a render of it.

Four steps, all idempotent:

1. Add the column with a ``'default'`` NOT NULL default (so the 179 existing
   ``gladlabs.io`` rows are correct by construction).
2. Backfill ``medium`` on the existing ``youtube`` rows from
   ``media_assets.type``, matched on the handle. This matters beyond tidiness:
   one surviving row (``fh4p6Y73LKA``) is a Short, and leaving it ``'default'``
   would make ``sync-metadata`` rebuild its title as long-form.
3. Swap ``UNIQUE (task_id, target)`` for ``UNIQUE (task_id, target, medium)``.
   No duplicates are possible at this point — the old constraint guaranteed
   ``(task_id, target)`` uniqueness and step 2 is a function of the row.
4. Backfill the rows that were overwritten, from
   ``media_assets.platform_video_ids->>'youtube'``. ``media_assets.updated_at``
   is the honest timestamp: ``_MERGE_PLATFORM_VIDEO_ID_SQL`` stamps it at the
   moment the handle was captured, and it matches the clobbered row's
   ``created_at`` to the microsecond in all five cases above.

Step 4 asserts nothing about whether the video still exists on the channel —
that is a network fact, not a schema one. ``youtube_metadata_sync`` demotes a
vanished upload to ``status='deleted'`` when the API says the video is gone.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ADD_COLUMN_SQL = """
    ALTER TABLE pipeline_distributions
      ADD COLUMN IF NOT EXISTS medium varchar NOT NULL DEFAULT 'default'
"""

# Which render each existing youtube row describes. The handle is the join key
# because it is the only thing the two tables agree on — pipeline_distributions
# never recorded the medium, which is the whole bug.
_BACKFILL_MEDIUM_SQL = """
    UPDATE pipeline_distributions pd
       SET medium = ma.type
      FROM media_assets ma
     WHERE pd.medium = 'default'
       AND pd.external_id IS NOT NULL
       AND ma.platform_video_ids->>'youtube' = pd.external_id
       AND ma.type IN ('video', 'video_short')
"""

_DROP_OLD_KEY_SQL = """
    ALTER TABLE pipeline_distributions
     DROP CONSTRAINT IF EXISTS pipeline_distributions_task_id_target_key
"""

# Named explicitly rather than left to Postgres' auto-naming so the ON CONFLICT
# target in dispatch_handles / pipeline_db has a stable constraint to bind to and
# a re-run of this migration can find it.
_ADD_NEW_KEY_SQL = """
    ALTER TABLE pipeline_distributions
      ADD CONSTRAINT pipeline_distributions_task_id_target_medium_key
      UNIQUE (task_id, target, medium)
"""

_NEW_KEY_EXISTS_SQL = """
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'pipeline_distributions'::regclass
       AND conname = 'pipeline_distributions_task_id_target_medium_key'
"""

# Reinstate the renders whose row was overwritten. The FK on task_id is why the
# join to pipeline_tasks is here rather than a bare INSERT ... SELECT: an asset
# whose task row was reaped would abort the whole statement.
_BACKFILL_ROWS_SQL = """
    INSERT INTO pipeline_distributions
        (task_id, target, medium, status, external_id, external_url,
         post_id, published_at, created_at)
    SELECT ma.task_id,
           'youtube',
           ma.type,
           'published',
           ma.platform_video_ids->>'youtube',
           'https://www.youtube.com/watch?v=' || (ma.platform_video_ids->>'youtube'),
           ma.post_id,
           ma.updated_at,
           ma.updated_at
      FROM media_assets ma
      JOIN pipeline_tasks pt ON pt.task_id = ma.task_id
     WHERE ma.type IN ('video', 'video_short')
       AND ma.task_id IS NOT NULL
       AND COALESCE(ma.platform_video_ids->>'youtube', '') <> ''
    ON CONFLICT (task_id, target, medium) DO NOTHING
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_ADD_COLUMN_SQL)
        typed = await conn.execute(_BACKFILL_MEDIUM_SQL)

        await conn.execute(_DROP_OLD_KEY_SQL)
        if not await conn.fetchval(_NEW_KEY_EXISTS_SQL):
            await conn.execute(_ADD_NEW_KEY_SQL)

        recovered = await conn.execute(_BACKFILL_ROWS_SQL)

    logger.info(
        "Migration pipeline_distributions.medium: column added, key re-keyed to "
        "(task_id, target, medium); medium backfill %s, orphaned-render backfill %s",
        typed, recovered,
    )


async def down(pool) -> None:
    """Restore the two-column key, dropping the rows it cannot hold.

    Narrowing a unique key is not a pure inverse: ``(task_id, target)`` cannot
    represent a task that delivered both renders, so this deletes the extra rows
    first — keeping the long form, which is what the pre-migration table
    happened to retain in every observed collision. That data loss is the reason
    the column itself is RETAINED: it is the only remaining record of which
    render each surviving row describes, and an unused ``'default'``-defaulted
    varchar costs nothing. Drop it by hand if a rollback is ever permanent.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM pipeline_distributions pd
             WHERE EXISTS (
                     SELECT 1 FROM pipeline_distributions other
                      WHERE other.task_id = pd.task_id
                        AND other.target = pd.target
                        AND (other.medium, other.id) < (pd.medium, pd.id)
                   )
            """
        )
        await conn.execute(
            "ALTER TABLE pipeline_distributions "
            "DROP CONSTRAINT IF EXISTS pipeline_distributions_task_id_target_medium_key"
        )
        already = await conn.fetchval(
            "SELECT 1 FROM pg_constraint "
            " WHERE conrelid = 'pipeline_distributions'::regclass "
            "   AND conname = 'pipeline_distributions_task_id_target_key'"
        )
        if not already:
            await conn.execute(
                "ALTER TABLE pipeline_distributions "
                "ADD CONSTRAINT pipeline_distributions_task_id_target_key "
                "UNIQUE (task_id, target)"
            )
    logger.warning(
        "Migration pipeline_distributions.medium rolled back: (task_id, target) "
        "key restored and duplicate-per-target rows DELETED. The medium column is "
        "RETAINED — it is the only record of which render each surviving row is."
    )
