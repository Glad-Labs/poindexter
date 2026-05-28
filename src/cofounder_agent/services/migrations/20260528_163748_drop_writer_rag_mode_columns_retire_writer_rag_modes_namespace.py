"""Drop writer_rag_mode columns — finish the atom migration.

The ``writer_rag_modes/`` directory was retired in PR #684 (4 dead
modes deleted; only TWO_PASS remained). This PR moves TWO_PASS into
``atoms/two_pass_writer.py`` and rewires ``generate_content`` to
call it directly via niche_slug routing, eliminating the dispatcher
indirection entirely.

That makes the ``writer_rag_mode`` columns on ``niches`` and
``pipeline_tasks`` meaningless — nothing reads them anymore. This
migration drops both, plus the associated CHECK constraint that PR
#684 narrowed to ``(NULL OR 'TWO_PASS')``.

Routing seam going forward: ``pipeline_tasks.niche_slug``. Tasks with
a niche_slug → ``atoms.two_pass_writer``; tasks without → legacy
``content_generator.generate_blog_post`` path.

Idempotent: ``DROP COLUMN IF EXISTS`` is a no-op when already gone.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_CONTENT_TASKS_VIEW_DDL_WITHOUT_WRITER_RAG_MODE = """
CREATE VIEW public.content_tasks AS
 SELECT pt.id,
    pt.task_id,
    pt.task_type,
    pt.task_type AS content_type,
    pv.title,
    pt.topic,
    pt.status,
    pt.stage,
    pt.style,
    pt.tone,
    pt.target_length,
    pt.category,
    pt.primary_keyword,
    pt.target_audience,
    pv.content,
    pv.excerpt,
    pv.featured_image_url,
    pv.quality_score,
    pv.qa_feedback,
    pv.seo_title,
    pv.seo_description,
    pv.seo_keywords,
    pt.percentage,
    pt.message,
    pt.model_used,
    pt.error_message,
    pv.models_used_by_phase,
    COALESCE((pv.stage_data -> 'metadata'::text), pv.stage_data) AS metadata,
    COALESCE((pv.stage_data -> 'result'::text), pv.stage_data) AS result,
    COALESCE((pv.stage_data -> 'task_metadata'::text), pv.stage_data) AS task_metadata,
    pt.site_id,
    pt.created_at,
    pt.updated_at,
    pt.started_at,
    pt.completed_at,
    ( SELECT (CASE
                WHEN pgh.event_kind = 'approved' THEN 'approved'
                WHEN pgh.event_kind LIKE 'rejected%' THEN 'rejected'
                ELSE pgh.event_kind
             END)::character varying
           FROM public.pipeline_gate_history pgh
          WHERE pgh.task_id = (pt.task_id)::text
            AND pgh.event_kind IN ('approved', 'rejected', 'rejected_retry', 'rejected_final')
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT (pgh.metadata ->> 'reviewer')::character varying
           FROM public.pipeline_gate_history pgh
          WHERE pgh.task_id = (pt.task_id)::text
            AND pgh.event_kind IN ('approved', 'rejected', 'rejected_retry', 'rejected_final')
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pgh.feedback
           FROM public.pipeline_gate_history pgh
          WHERE pgh.task_id = (pt.task_id)::text
            AND pgh.event_kind IN ('approved', 'rejected', 'rejected_retry', 'rejected_final')
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS human_feedback,
    ( SELECT pd.post_id
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_id,
    ( SELECT pd.post_slug
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS post_slug,
    ( SELECT pd.published_at
           FROM public.pipeline_distributions pd
          WHERE (((pd.task_id)::text = (pt.task_id)::text) AND ((pd.target)::text = 'gladlabs.io'::text))
         LIMIT 1) AS published_at,
    pt.awaiting_gate,
    pt.gate_artifact,
    pt.gate_paused_at,
    pt.niche_slug,
    pt.topic_batch_id
   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))))
"""


async def up(pool) -> None:
    """Drop niches.writer_rag_mode + pipeline_tasks.writer_rag_mode.

    The ``content_tasks`` view exposes ``pipeline_tasks.writer_rag_mode``,
    so we drop the view first, drop the columns, then recreate the view
    without that column. PostgreSQL's ``CREATE OR REPLACE VIEW`` can't
    remove columns from an existing view, hence the drop+recreate.
    """
    async with pool.acquire() as conn:
        # 1. Drop the content_tasks view (it references writer_rag_mode).
        await conn.execute("DROP VIEW IF EXISTS public.content_tasks")
        # 2. Drop the CHECK constraint on niches (explicit; column drop
        #    cascades it anyway but makes the audit trail clearer).
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS niches_writer_rag_mode_check"
        )
        # 3. Drop the columns.
        result_niches = await conn.execute(
            "ALTER TABLE niches DROP COLUMN IF EXISTS writer_rag_mode"
        )
        result_tasks = await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS writer_rag_mode"
        )
        # 4. Recreate content_tasks without writer_rag_mode.
        await conn.execute(_CONTENT_TASKS_VIEW_DDL_WITHOUT_WRITER_RAG_MODE)
        # 5. Reattach the INSTEAD OF triggers — the baseline schema attaches
        #    insert/update/delete redirect triggers so the view behaves like
        #    a table. DROP VIEW removes them; we have to recreate them.
        #    The redirect functions themselves survive (they're top-level).
        await conn.execute(
            "CREATE TRIGGER content_tasks_delete_trigger "
            "INSTEAD OF DELETE ON public.content_tasks "
            "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_delete_redirect()"
        )
        await conn.execute(
            "CREATE TRIGGER content_tasks_insert_trigger "
            "INSTEAD OF INSERT ON public.content_tasks "
            "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_insert_redirect()"
        )
        await conn.execute(
            "CREATE TRIGGER content_tasks_update_trigger "
            "INSTEAD OF UPDATE ON public.content_tasks "
            "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_update_redirect()"
        )
        logger.info(
            "Migration drop_writer_rag_mode_columns: niches=%s pipeline_tasks=%s",
            result_niches, result_tasks,
        )


async def down(pool) -> None:
    """No-op revert.

    Restoring the columns would point ``niches.writer_rag_mode`` at a
    dispatcher (``writer_rag_modes/__init__.py``) that no longer
    exists. Recovery from this state requires reverting the
    application code, not just the migration.
    """
    logger.info(
        "Migration drop_writer_rag_mode_columns down: no-op "
        "(restoring the columns alone wouldn't recreate the deleted "
        "dispatcher; revert the application code if needed)"
    )
