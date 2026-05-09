"""Migration 20260509_125415: unify approval history on pipeline_gate_history.

ISSUE: Glad-Labs/poindexter#366

Background — for almost a year the codebase carried two parallel
approval-history tables:

  - ``pipeline_reviews`` — the legacy table written by the route
    handlers (approve_task / reject_task / publish_task / publish-time
    auto-rejection paths).
  - ``pipeline_gate_history`` — the typed gate-history table written by
    ``services/approval_service.py`` and the gate-aware HITL stages.

The ``content_tasks`` view sourced ``approval_status`` / ``approved_by``
/ ``human_feedback`` exclusively from ``pipeline_reviews`` via scalar
subqueries. The 2026-05-09 audit confirmed ``pipeline_gate_history`` was
being written by the new gate flow but never read — and the route
writers' rows in ``pipeline_reviews`` had no analogue in the gate
table. Two writers, one reader, no unification.

This migration:

1. Backfills ``pipeline_gate_history`` from existing ``pipeline_reviews``
   rows so prod's 1,345-row audit trail survives the cutover (decision
   ``approved`` -> event_kind ``approved``; ``rejected`` -> ``rejected``;
   anything else recorded verbatim with the original decision under
   ``metadata.decision``). The historical rows use a ``backfill``
   gate_name so they can be distinguished from forward-going writes,
   which use ``final_approval``.
2. Rewrites ``content_tasks`` AND ``pipeline_tasks_view`` to source
   approval_status / approved_by / human_feedback from
   ``pipeline_gate_history`` instead — keyed on the latest row per
   ``task_id``. event_kind is mapped to the public approval_status
   semantic via CASE: ``approved`` -> ``approved``, anything starting
   with ``rejected`` -> ``rejected``, everything else passes through.
3. Leaves ``pipeline_reviews`` in place so the next deployment can roll
   back if needed. The table is dropped in a follow-up commit
   (Phase 3) once verification confirms the new view shape works.

The view rewrites are non-destructive ``CREATE OR REPLACE`` calls — the
column list is preserved exactly so downstream consumers (worker queries,
Grafana panels, MCP tools) keep working without changes.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backfill — copy historical pipeline_reviews rows into pipeline_gate_history.
# ---------------------------------------------------------------------------
#
# The backfill is keyed on (task_id, gate_name='backfill', created_at) and
# guarded by NOT EXISTS so re-running the migration doesn't duplicate
# rows. We deliberately use a distinct gate_name (``backfill``) so post-
# migration writes (gate_name=``final_approval``) and historical rows
# stay separable for forensic queries.
_BACKFILL_SQL = """
INSERT INTO pipeline_gate_history
    (task_id, gate_name, event_kind, feedback, metadata, created_at)
SELECT
    pr.task_id,
    'backfill' AS gate_name,
    CASE
        WHEN pr.decision = 'approved' THEN 'approved'
        WHEN pr.decision = 'rejected' THEN 'rejected'
        ELSE pr.decision
    END AS event_kind,
    pr.feedback,
    jsonb_build_object(
        'reviewer', pr.reviewer,
        'decision', pr.decision,
        'version', pr.version,
        'source', 'pipeline_reviews_backfill'
    ) AS metadata,
    pr.created_at
FROM pipeline_reviews pr
WHERE NOT EXISTS (
    -- Skip rows already mirrored — idempotent across migration re-runs.
    SELECT 1
      FROM pipeline_gate_history pgh
     WHERE pgh.task_id = pr.task_id
       AND pgh.gate_name = 'backfill'
       AND pgh.created_at = pr.created_at
)
"""


# ---------------------------------------------------------------------------
# View rewrites — content_tasks and pipeline_tasks_view both replace their
# pipeline_reviews scalar subqueries with pipeline_gate_history equivalents.
#
# Column order and names are preserved exactly so consumers don't need to
# update.
# ---------------------------------------------------------------------------

_CONTENT_TASKS_VIEW_DDL = """
CREATE OR REPLACE VIEW public.content_tasks AS
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
    pt.writer_rag_mode,
    pt.topic_batch_id
   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))))
"""


_PIPELINE_TASKS_VIEW_DDL = """
CREATE OR REPLACE VIEW public.pipeline_tasks_view AS
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
         LIMIT 1) AS published_at
   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))))
"""


async def up(pool) -> None:
    """Apply the migration.

    Three steps:

    1. Backfill pipeline_gate_history from pipeline_reviews (idempotent —
       NOT EXISTS guard prevents duplicate rows on re-run).
    2. CREATE OR REPLACE the content_tasks view to source from
       pipeline_gate_history.
    3. CREATE OR REPLACE the pipeline_tasks_view view (same shape, kept
       in lockstep so legacy consumers don't drift).

    pipeline_reviews itself is NOT dropped here — that lives in Phase 3
    once the view rewrite has been verified working in production.
    """
    async with pool.acquire() as conn:
        # Step 1 — backfill. Skip cleanly if pipeline_reviews has been
        # dropped already (re-running migration after Phase 3 landed).
        try:
            backfilled = await conn.execute(_BACKFILL_SQL)
            logger.info("[unify_approval_history] backfill: %s", backfilled)
        except Exception as exc:
            # Most common case: pipeline_reviews no longer exists (Phase 3
            # already ran). Log + continue — the view rewrite is what
            # matters.
            logger.warning(
                "[unify_approval_history] backfill skipped: %s",
                exc,
            )

        # Step 2 — content_tasks view rewrite.
        await conn.execute(_CONTENT_TASKS_VIEW_DDL)
        logger.info("[unify_approval_history] content_tasks view rewritten")

        # Step 3 — pipeline_tasks_view rewrite (same shape, legacy alias).
        await conn.execute(_PIPELINE_TASKS_VIEW_DDL)
        logger.info("[unify_approval_history] pipeline_tasks_view rewritten")

        logger.info(
            "[unify_approval_history] applied — content_tasks now sources "
            "approval_status from pipeline_gate_history",
        )


async def down(pool) -> None:
    """Refuse to revert — once the writers + view are unified, going back
    requires a real schema migration, not a flip.

    The forward path is: rewrite the view to read pipeline_reviews,
    re-deploy the route handlers to write to pipeline_reviews, run a
    reverse-direction backfill, drop the new rows from
    pipeline_gate_history that came from forward-going writes (these
    have gate_name=``final_approval``). That's a real piece of work,
    not a one-line reversal — and we'd only do it if the new view shape
    blew up in prod, in which case we'd write a forward migration to
    fix it instead.
    """
    raise NotImplementedError(
        "20260509_125415_unify_approval_history is irreversible — once "
        "writers and reader are unified on pipeline_gate_history, going "
        "back requires a real reverse migration. Drop the database to "
        "start over, or write a new forward migration to fix any issue."
    )
