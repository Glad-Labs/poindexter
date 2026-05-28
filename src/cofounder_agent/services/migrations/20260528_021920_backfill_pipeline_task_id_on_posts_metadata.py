"""Migration 20260528_021920: backfill pipeline task id on posts metadata.

ISSUE: status drift between ``posts`` and ``pipeline_tasks`` — when
``scheduled_publisher`` promoted ``posts.status='scheduled' →
'published'``, the linked ``pipeline_tasks.status`` stayed stuck at
``approved`` forever. Three rows were verified stale live 2026-05-28
(see PR body).

The root cause is that ``posts`` had no first-class column for the
source task id. The codebase relied on slug-suffix archaeology
(``RIGHT(p.slug, 8)`` matched against ``pipeline_tasks.task_id``) to
re-link the two tables, which works for one-shot migrations but is
hostile to runtime queries — particularly the per-row UPDATE
``scheduled_publisher`` needs every minute.

Per ``feedback_filter_on_seams_not_slugs``: when a structured field
exists, populate + filter on it. This migration establishes
``posts.metadata->>'pipeline_task_id'`` as the canonical seam. The
column already exists (``posts.metadata jsonb DEFAULT '{}'::jsonb``);
the change is purely additive — a new JSONB key — so no schema DDL
and no backcompat shim is needed beyond callers tolerating NULL
during the backfill window.

Steps (all idempotent):

1. **Stamp ``metadata->>'pipeline_task_id'`` on existing posts.**
   For every ``posts`` row whose ``metadata`` doesn't already carry the
   key, look up the matching ``pipeline_tasks`` row via slug-suffix
   archaeology (``RIGHT(p.slug, 8) || '%'`` against
   ``pipeline_tasks.task_id``). Only populate when **exactly one**
   ``pipeline_tasks`` row matches — ambiguous joins (very rare; would
   require two task ids sharing an 8-hex prefix) stay NULL so step 2
   skips them too. The matching task id is written into ``metadata``
   via ``jsonb_set``, which preserves all other keys (devto_*,
   featured_image_data, etc.).

2. **Sync ``pipeline_tasks.status`` for published posts.** Now that the
   seam is populated, JOIN ``pipeline_tasks`` to ``posts`` via the new
   JSONB key. For every published post whose linked task is still
   ``approved`` or ``scheduled``, flip the task to ``published`` and
   bump ``updated_at``. This is the actual user-visible fix — the
   ``poindexter tasks list`` CLI shows the task status, so stale tasks
   make the operator surface lie.

Going forward (in the same PR):

- ``publish_service.publish_post_from_task`` populates
  ``posts.metadata->>'pipeline_task_id'`` at insert time so new posts
  carry the seam from the start.
- ``scheduled_publisher`` reads ``metadata->>'pipeline_task_id'`` from
  the CTE row and issues a second UPDATE against ``pipeline_tasks`` in
  the same transaction.

Idempotent — step 1's WHERE clause skips rows that already have the
key; step 2's WHERE clause skips tasks already at ``status='published'``.
A replay against a converged DB is a no-op modulo the
``schema_migrations`` self-record write.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Step 1: stamp pipeline_task_id onto posts.metadata for
            # rows that don't already carry the key. Slug-suffix
            # archaeology (RIGHT(p.slug, 8) → pipeline_tasks.task_id)
            # is the same pattern used in
            # 20260519_134736_niches_default_media_to_generate.py.
            # Only populate when EXACTLY ONE pipeline_tasks row matches
            # the slug suffix — ambiguous matches stay NULL so we don't
            # mis-link a post to the wrong task.
            stamp_result = await conn.execute(
                """
                UPDATE posts p
                   SET metadata = jsonb_set(
                           COALESCE(p.metadata, '{}'::jsonb),
                           '{pipeline_task_id}',
                           to_jsonb(matched.task_id),
                           true
                       ),
                       updated_at = NOW()
                  FROM (
                      SELECT p2.id AS post_id,
                             MIN(pt.task_id) AS task_id,
                             COUNT(*) AS match_count
                        FROM posts p2
                        JOIN pipeline_tasks pt
                          ON pt.task_id LIKE RIGHT(p2.slug, 8) || '%'
                       WHERE (p2.metadata IS NULL
                              OR NOT (p2.metadata ? 'pipeline_task_id'))
                         AND p2.slug IS NOT NULL
                         AND length(p2.slug) >= 8
                       GROUP BY p2.id
                      HAVING COUNT(*) = 1
                  ) matched
                 WHERE p.id = matched.post_id
                """
            )
            logger.info(
                "[migration] posts.metadata pipeline_task_id stamp: %s",
                stamp_result,
            )

            # Step 2: sync pipeline_tasks.status='published' for every
            # post that's already published whose linked task is still
            # in an earlier state. Reads through the seam populated in
            # step 1 (metadata->>'pipeline_task_id'). Restricted to
            # 'approved' / 'scheduled' source statuses so we don't
            # clobber 'rejected' / 'failed' / 'cancelled' tasks whose
            # post somehow ended up published anyway (operator edge
            # cases — surface to a human, don't auto-resolve).
            sync_result = await conn.execute(
                """
                UPDATE pipeline_tasks pt
                   SET status = 'published',
                       updated_at = NOW()
                  FROM posts p
                 WHERE p.metadata ->> 'pipeline_task_id' = pt.task_id
                   AND p.status = 'published'
                   AND pt.status IN ('approved', 'scheduled')
                """
            )
            logger.info(
                "[migration] pipeline_tasks.status sync to published: %s",
                sync_result,
            )

    logger.info(
        "[migration] backfill_pipeline_task_id_on_posts_metadata: complete"
    )


async def down(pool) -> None:
    """Revert the migration.

    One-way backfill — leaving the JSONB key + the synced
    pipeline_tasks rows in place is the right rollback shape. The key
    is additive and the status sync reflects reality (the posts ARE
    published). Documenting as a no-op rather than tearing the seam
    out, which would re-introduce the bug.
    """
    logger.info(
        "[migration] backfill_pipeline_task_id_on_posts_metadata down: "
        "no-op (one-way backfill)"
    )
