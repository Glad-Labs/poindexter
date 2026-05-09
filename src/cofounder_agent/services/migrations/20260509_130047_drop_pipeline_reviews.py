"""Migration 20260509_130047: drop pipeline_reviews.

ISSUE: Glad-Labs/poindexter#366

Background — Phase 3 of the pipeline_reviews -> pipeline_gate_history
unification. The previous migration (``20260509_125415_unify_approval_history``)
backfilled the historical rows and rewrote the ``content_tasks`` /
``pipeline_tasks_view`` views so approval_status / approved_by /
human_feedback now resolve through ``pipeline_gate_history``. Phase 1
migrated all route writers off ``PipelineDB.add_review`` to direct
``pipeline_gate_history`` INSERTs.

Nothing reads or writes ``pipeline_reviews`` anymore. This migration
removes the table itself + supporting indexes / sequence / FK so the
schema reflects reality.

Safety:

- ``CASCADE`` on the table drop tears down the FK
  (``pipeline_reviews_task_id_fkey``) and the supporting indexes
  (``idx_pipeline_reviews_task``, ``idx_pipeline_reviews_task_decision``)
  cleanly. The sequence (``pipeline_reviews_id_seq``) is OWNED BY the
  ``id`` column so it goes with the table.
- The migration uses ``IF EXISTS`` so re-runs against a DB where the
  table is already gone are no-ops.
- The previous migration's backfill ran with ``NOT EXISTS`` guard, so
  the historical rows are already mirrored under
  ``gate_name='backfill'`` in ``pipeline_gate_history`` before this
  drop runs.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Drop pipeline_reviews and supporting objects.

    Idempotent — IF EXISTS guards every drop so re-running the
    migration on a DB where the table is already gone is a no-op.
    """
    async with pool.acquire() as conn:
        # Sanity-check the previous migration ran. If the views still
        # reference pipeline_reviews, dropping the table would break
        # them. This guard fails loud rather than silently corrupting.
        view_def_uses_old_table = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM pg_views
                 WHERE schemaname = 'public'
                   AND viewname IN ('content_tasks', 'pipeline_tasks_view')
                   AND definition ILIKE '%pipeline_reviews%'
            )
            """
        )
        if view_def_uses_old_table:
            raise RuntimeError(
                "Refusing to drop pipeline_reviews — content_tasks or "
                "pipeline_tasks_view still references it. Run "
                "20260509_125415_unify_approval_history.py first."
            )

        # CASCADE clears the FK + the two btree indexes. The id sequence
        # is OWNED BY the column so it goes with the table.
        await conn.execute("DROP TABLE IF EXISTS public.pipeline_reviews CASCADE")
        logger.info("[drop_pipeline_reviews] pipeline_reviews dropped")


async def down(pool) -> None:
    """Refuse to revert.

    The data is in ``pipeline_gate_history`` under
    ``gate_name='backfill'`` — recreating ``pipeline_reviews`` from
    that subset is a real piece of work, not a one-line reversal. If we
    ever needed to reverse, write a fresh forward migration that
    materialises the table from the gate_history rows.
    """
    raise NotImplementedError(
        "20260509_130047_drop_pipeline_reviews is irreversible — the "
        "data lives in pipeline_gate_history under gate_name='backfill'. "
        "Write a forward migration if you need a pipeline_reviews-shaped "
        "view of those rows."
    )
