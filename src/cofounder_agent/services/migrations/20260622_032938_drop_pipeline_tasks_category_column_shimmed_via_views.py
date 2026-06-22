"""Migration 20260622_032938: drop pipeline_tasks.category (shimmed via views)

ISSUE: Glad-Labs/poindexter#796

``pipeline_tasks.category`` is a vestigial column. 56% of rows are NULL because
the modern creation paths (topic-batch generation, SEO-refresh enqueue, the
April writer-model bake-off runs) never populated it, and nothing downstream
depends on it: live-site categorisation is driven by ``posts.category_id``
(100% populated), and pipeline routing is driven by ``template_slug`` +
``niche_slug``. The column was superseded by ``niche_slug``.

This is a **shimmed drop** (per operator decision): the physical column goes,
but the read surfaces stay intact as no-op shims so nothing downstream 500s
(honours the back-compat-shims rule):

  * The ``content_tasks`` and ``pipeline_tasks_view`` views keep a
    ``NULL::character varying AS category`` column, so ``SELECT *`` consumers,
    ``TaskRecord.category``, the ``GET /tasks?category=`` filter + ILIKE search,
    and the analytics column list all keep working (the filter simply matches
    nothing now, which is fine for a retired field).
  * The two write-through INSTEAD OF trigger functions
    (``content_tasks_insert_redirect`` / ``content_tasks_update_redirect``) stop
    writing ``category`` to the base table, so ``UPDATE content_tasks SET
    category=…`` and ``INSERT INTO content_tasks (…, category) …`` are accepted
    but ignored.

The six direct-table INSERTs that named ``category`` are updated in the same PR
(``tasks_db.add_task`` / ``bulk_add_tasks``, ``pipeline_db.upsert_task``,
``topic_discovery``, ``topic_proposal_service``, ``jobs/run_dev_diary_post``) —
without those edits the column drop would make every such INSERT raise
``UndefinedColumnError``.

Mechanics: the two views reference ``pt.category`` and the two trigger functions
read/write it, so the column can't be dropped until they're rebuilt. We DROP
both views (which also drops the three INSTEAD OF triggers on ``content_tasks``),
``CREATE OR REPLACE`` the two trigger functions without ``category``, drop the
column, then recreate the views (with the NULL shim) and re-attach the triggers.
The view bodies below are copied verbatim from ``0000_baseline.schema.sql`` with
the single substitution ``pt.category`` -> ``NULL::character varying AS category``.

IMPORTANT: stdlib-only (no app imports) so the migrations-smoke CI step can apply
it against a fresh DB without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# --- trigger functions, rewritten without `category` -----------------------

_INSERT_REDIRECT_FN = """
CREATE OR REPLACE FUNCTION public.content_tasks_insert_redirect() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage, style, tone, target_length, primary_keyword, target_audience, percentage, message, model_used, error_message, created_at, updated_at)
    VALUES (NEW.task_id, COALESCE(NEW.task_type, 'blog_post'), COALESCE(NEW.topic, NEW.title, 'untitled'), COALESCE(NEW.status, 'pending'), COALESCE(NEW.stage, 'pending'), COALESCE(NEW.style, 'technical'), COALESCE(NEW.tone, 'professional'), COALESCE(NEW.target_length, 1500), NEW.primary_keyword, NEW.target_audience, COALESCE(NEW.percentage, 0), NEW.message, NEW.model_used, NEW.error_message, COALESCE(NEW.created_at, NOW()), COALESCE(NEW.updated_at, NOW()))
    ON CONFLICT (task_id) DO NOTHING;

    INSERT INTO pipeline_versions (task_id, version, title, content, excerpt, featured_image_url, seo_title, seo_description, seo_keywords, quality_score, qa_feedback, models_used_by_phase, stage_data)
    VALUES (NEW.task_id, 1, NEW.title, NEW.content, NEW.excerpt, NEW.featured_image_url, NEW.seo_title, NEW.seo_description, NEW.seo_keywords, NEW.quality_score, NEW.qa_feedback, COALESCE(NEW.models_used_by_phase, '{}'),
        jsonb_strip_nulls(jsonb_build_object('metadata', NEW.metadata, 'result', NEW.result, 'task_metadata', NEW.task_metadata)))
    ON CONFLICT (task_id, version) DO UPDATE SET
        title = COALESCE(EXCLUDED.title, pipeline_versions.title),
        content = COALESCE(EXCLUDED.content, pipeline_versions.content),
        excerpt = COALESCE(EXCLUDED.excerpt, pipeline_versions.excerpt),
        featured_image_url = COALESCE(EXCLUDED.featured_image_url, pipeline_versions.featured_image_url),
        seo_title = COALESCE(EXCLUDED.seo_title, pipeline_versions.seo_title),
        seo_description = COALESCE(EXCLUDED.seo_description, pipeline_versions.seo_description),
        seo_keywords = COALESCE(EXCLUDED.seo_keywords, pipeline_versions.seo_keywords),
        quality_score = COALESCE(EXCLUDED.quality_score, pipeline_versions.quality_score),
        qa_feedback = COALESCE(EXCLUDED.qa_feedback, pipeline_versions.qa_feedback),
        stage_data = pipeline_versions.stage_data || EXCLUDED.stage_data;

    RETURN NEW;
END;
$$;
"""

_UPDATE_REDIRECT_FN = """
CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE pipeline_tasks SET
        status = NEW.status,
        stage = COALESCE(NEW.stage, pipeline_tasks.stage),
        percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
        message = COALESCE(NEW.message, pipeline_tasks.message),
        model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
        error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
        style = COALESCE(NEW.style, pipeline_tasks.style),
        tone = COALESCE(NEW.tone, pipeline_tasks.tone),
        target_audience = COALESCE(NEW.target_audience, pipeline_tasks.target_audience),
        primary_keyword = COALESCE(NEW.primary_keyword, pipeline_tasks.primary_keyword),
        target_length = COALESCE(NEW.target_length, pipeline_tasks.target_length),
        updated_at = COALESCE(NEW.updated_at, NOW()),
        started_at = COALESCE(NEW.started_at, pipeline_tasks.started_at),
        completed_at = CASE
            WHEN NEW.status IN ('published','failed','cancelled','rejected','rejected_final')
            THEN NOW()
            ELSE pipeline_tasks.completed_at
        END
    WHERE task_id = NEW.task_id;

    UPDATE pipeline_versions SET
        title = COALESCE(NEW.title, pipeline_versions.title),
        content = COALESCE(NEW.content, pipeline_versions.content),
        excerpt = COALESCE(NEW.excerpt, pipeline_versions.excerpt),
        featured_image_url = COALESCE(NEW.featured_image_url, pipeline_versions.featured_image_url),
        seo_title = COALESCE(NEW.seo_title, pipeline_versions.seo_title),
        seo_description = COALESCE(NEW.seo_description, pipeline_versions.seo_description),
        seo_keywords = COALESCE(NEW.seo_keywords, pipeline_versions.seo_keywords),
        quality_score = COALESCE(NEW.quality_score, pipeline_versions.quality_score),
        qa_feedback = COALESCE(NEW.qa_feedback, pipeline_versions.qa_feedback),
        models_used_by_phase = COALESCE(NEW.models_used_by_phase, pipeline_versions.models_used_by_phase),
        stage_data = pipeline_versions.stage_data || jsonb_strip_nulls(
            jsonb_build_object(
                'metadata', NEW.metadata,
                'result', NEW.result,
                'task_metadata', NEW.task_metadata
            )
        )
    WHERE task_id = NEW.task_id AND version = 1;

    RETURN NEW;
END;
$$;
"""


# --- views, rebuilt with `NULL::character varying AS category` shim ---------
#
# The correlated-subquery / JOIN tail is shared by both views; only the
# leading column projection differs (content_tasks carries the gate +
# niche_slug + topic_batch_id columns; pipeline_tasks_view stops at
# published_at). Copied verbatim from 0000_baseline.schema.sql.

_VIEW_TAIL_SUBQUERIES = """    ( SELECT (
                CASE
                    WHEN (pgh.event_kind = 'approved'::text) THEN 'approved'::text
                    WHEN (pgh.event_kind ~~ 'rejected%'::text) THEN 'rejected'::text
                    ELSE pgh.event_kind
                END)::character varying AS event_kind
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approval_status,
    ( SELECT ((pgh.metadata ->> 'reviewer'::text))::character varying AS "varchar"
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
          ORDER BY pgh.created_at DESC
         LIMIT 1) AS approved_by,
    ( SELECT pgh.feedback
           FROM public.pipeline_gate_history pgh
          WHERE ((pgh.task_id = (pt.task_id)::text) AND (pgh.event_kind = ANY (ARRAY['approved'::text, 'rejected'::text, 'rejected_retry'::text, 'rejected_final'::text])))
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
         LIMIT 1) AS published_at"""

_VIEW_FROM = """   FROM (public.pipeline_tasks pt
     LEFT JOIN public.pipeline_versions pv ON ((((pv.task_id)::text = (pt.task_id)::text) AND (pv.version = ( SELECT max(pipeline_versions.version) AS max
           FROM public.pipeline_versions
          WHERE ((pipeline_versions.task_id)::text = (pt.task_id)::text))))));"""

_CONTENT_TASKS_VIEW = f"""
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
    NULL::character varying AS category,
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
{_VIEW_TAIL_SUBQUERIES},
    pt.awaiting_gate,
    pt.gate_artifact,
    pt.gate_paused_at,
    pt.niche_slug,
    pt.topic_batch_id
{_VIEW_FROM}
"""

_PIPELINE_TASKS_VIEW = f"""
CREATE VIEW public.pipeline_tasks_view AS
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
    NULL::character varying AS category,
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
{_VIEW_TAIL_SUBQUERIES}
{_VIEW_FROM}
"""

_TRIGGERS = (
    "CREATE TRIGGER content_tasks_insert_trigger INSTEAD OF INSERT ON public.content_tasks "
    "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_insert_redirect();",
    "CREATE TRIGGER content_tasks_update_trigger INSTEAD OF UPDATE ON public.content_tasks "
    "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_update_redirect();",
    "CREATE TRIGGER content_tasks_delete_trigger INSTEAD OF DELETE ON public.content_tasks "
    "FOR EACH ROW EXECUTE FUNCTION public.content_tasks_delete_redirect();",
)


async def up(pool) -> None:
    """Drop ``pipeline_tasks.category`` and rebuild the dependent views/triggers.

    Idempotent: ``DROP VIEW IF EXISTS`` + ``DROP COLUMN IF EXISTS`` make a
    re-run a no-op, and on a fresh DB the baseline creates the column + the
    pt.category views first, so this transforms them into the NULL-shim form.
    Runs in one transaction so a failure leaves the schema untouched.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Drop the two views. Dropping content_tasks also drops its three
            #    INSTEAD OF triggers; pipeline_tasks_view has none.
            await conn.execute("DROP VIEW IF EXISTS public.pipeline_tasks_view;")
            await conn.execute("DROP VIEW IF EXISTS public.content_tasks;")

            # 2. Rewrite the write-through trigger functions without `category`.
            await conn.execute(_INSERT_REDIRECT_FN)
            await conn.execute(_UPDATE_REDIRECT_FN)

            # 3. Drop the vestigial column.
            await conn.execute(
                "ALTER TABLE public.pipeline_tasks DROP COLUMN IF EXISTS category;"
            )

            # 4. Recreate the views with the NULL::varchar category shim.
            await conn.execute(_CONTENT_TASKS_VIEW)
            await conn.execute(_PIPELINE_TASKS_VIEW)

            # 5. Re-attach the INSTEAD OF triggers to content_tasks.
            for trigger_sql in _TRIGGERS:
                await conn.execute(trigger_sql)

    logger.info(
        "Migration drop_pipeline_tasks_category_column_shimmed_via_views: "
        "dropped pipeline_tasks.category; views/triggers rebuilt with NULL shim."
    )


async def down(pool) -> None:
    """One-way migration — intentionally not reverted.

    Re-adding the column would only restore an all-NULL vestige (the data is
    not recoverable, and the write paths that populated it are removed in the
    same PR). The view shim keeps ``category`` readable as NULL, so consumers
    are unaffected by the absence of a revert. Operators who genuinely need the
    historical values back should restore from a pre-migration backup. Same
    posture as 20260620_054135_retire_orphaned_ops_triage_system_prompt.
    """
    logger.warning(
        "Migration drop_pipeline_tasks_category_column_shimmed_via_views down: "
        "no-op — refusing to re-add an unrecoverable all-NULL vestigial column."
    )
