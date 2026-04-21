"""
Migration 0078: Propagate ``models_used_by_phase`` through the content_tasks
UPDATE redirect.

Context:
    Migration 0077 closed the ``started_at`` gap on the trigger. An audit
    of every ``update_task()`` caller found one more column the trigger
    silently dropped on subsequent updates: ``models_used_by_phase`` on
    ``pipeline_versions``. The INSERT redirect (content_tasks_insert_redirect)
    and ``pipeline_db.upsert_version()`` both write the column correctly,
    but later ``update_task(task_id, {"models_used_by_phase": {...}})`` calls
    lose their payload because the UPDATE redirect's SET clause never listed it.

Evidence at migration time:
    199/363 pipeline_versions rows had models_used_by_phase populated —
    the ones set at INSERT. Updates refining the phase→model map after
    later stages (writer fallback, QA rewrite, etc.) never landed.

Other audit findings (NOT fixed here — deliberate):
    - ``approval_status``, ``human_feedback`` are view-computed via scalar
      subqueries on pipeline_reviews. UPDATE callers should migrate to
      pipeline_db.add_review() (done in Phase 1 of gitea#271).
    - ``featured_image_photographer`` / ``featured_image_source`` /
      ``publish_mode`` / ``recent_image_urls`` have no base-table column
      and already route to task_metadata JSONB via the _VIEW_COLUMNS
      allowlist in services/tasks_db.py.

This migration is idempotent — ``CREATE OR REPLACE FUNCTION`` rewrites
the trigger body without disturbing the trigger binding.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE pipeline_tasks SET
        status = NEW.status,
        stage = COALESCE(NEW.stage, pipeline_tasks.stage),
        percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
        message = COALESCE(NEW.message, pipeline_tasks.message),
        model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
        error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
        category = COALESCE(NEW.category, pipeline_tasks.category),
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
$function$;
"""


SQL_DOWN = """
CREATE OR REPLACE FUNCTION public.content_tasks_update_redirect()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE pipeline_tasks SET
        status = NEW.status,
        stage = COALESCE(NEW.stage, pipeline_tasks.stage),
        percentage = COALESCE(NEW.percentage, pipeline_tasks.percentage),
        message = COALESCE(NEW.message, pipeline_tasks.message),
        model_used = COALESCE(NEW.model_used, pipeline_tasks.model_used),
        error_message = COALESCE(NEW.error_message, pipeline_tasks.error_message),
        category = COALESCE(NEW.category, pipeline_tasks.category),
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
$function$;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname = 'content_tasks_update_redirect'"
        )
        if not exists:
            logger.info(
                "content_tasks_update_redirect() not present — skipping 0078"
            )
            return
        await conn.execute(SQL_UP)
        logger.info(
            "Rewrote content_tasks_update_redirect() to propagate models_used_by_phase"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname = 'content_tasks_update_redirect'"
        )
        if not exists:
            return
        await conn.execute(SQL_DOWN)
        logger.info("Reverted content_tasks_update_redirect() to pre-0078 state")
