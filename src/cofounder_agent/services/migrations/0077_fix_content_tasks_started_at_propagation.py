"""
Migration 0077: Propagate ``started_at`` through the content_tasks UPDATE redirect.

Root cause:
    TaskExecutor passes ``{"status": "in_progress", "started_at": _now, ...}``
    to ``update_task``. ``content_tasks`` is a VIEW so the write goes
    through the ``content_tasks_update_redirect`` INSTEAD OF trigger,
    whose UPDATE of ``pipeline_tasks`` never listed ``started_at``.
    Result: every row has ``started_at = NULL`` (362/362 at time of
    discovery), so any ops query that segments by ``started_at`` is
    blind to pickup latency.

Fix:
    Add ``started_at = COALESCE(NEW.started_at, pipeline_tasks.started_at)``
    so the caller's value lands on the base table, and backfill historical
    NULL rows from their ``created_at`` as a reasonable approximation
    (the DB cannot recover the true pickup timestamp).

This migration is idempotent — ``CREATE OR REPLACE FUNCTION`` rewrites
the trigger body; the backfill runs once but is a no-op when there are
no NULL rows.
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


SQL_BACKFILL = """
UPDATE pipeline_tasks
SET started_at = created_at
WHERE started_at IS NULL
  AND status IN ('in_progress','awaiting_approval','published','rejected','rejected_final','failed','cancelled');
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
                "content_tasks_update_redirect() not present — skipping 0077"
            )
            return
        await conn.execute(SQL_UP)
        backfilled = await conn.execute(SQL_BACKFILL)
        logger.info(
            "Rewrote content_tasks_update_redirect() to propagate started_at "
            "and backfilled historical NULLs (%s)",
            backfilled,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname = 'content_tasks_update_redirect'"
        )
        if not exists:
            return
        await conn.execute(SQL_DOWN)
        logger.info("Reverted content_tasks_update_redirect() to pre-0077 state")
