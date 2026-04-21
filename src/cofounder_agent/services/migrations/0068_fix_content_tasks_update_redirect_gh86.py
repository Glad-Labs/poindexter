"""
Migration 0068: Fix content_tasks UPDATE trigger to propagate
qa_feedback + category (GH-86).

Root cause of GH-86:
    content_tasks is a VIEW over pipeline_tasks JOIN pipeline_versions
    (hybrid architecture from migration 0057). INSTEAD OF UPDATE is
    handled by content_tasks_update_redirect(), which was written
    pre-GH-86 and omits three columns the pipeline writes:

      - pipeline_tasks.category         (stayed NULL on 7/10 pending)
      - pipeline_versions.qa_feedback   (stayed NULL on 10/10 pending)

    INSERT redirect handles both correctly. Only UPDATE was broken.

Fix: recreate the trigger function with COALESCE(NEW.col,
pipeline_X.col) for both missing columns so subsequent writes land on
the base tables. The COALESCE preserves any value already present when
the pipeline sends NULL (prevents a second stage from accidentally
blanking a column a first stage already populated).

Rollback restores the prior function definition.
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
        # Only run if the trigger function actually exists — safe on
        # fresh DBs where content_tasks may still be a real table.
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname = 'content_tasks_update_redirect'"
        )
        if not exists:
            logger.info(
                "content_tasks_update_redirect() not present — skipping 0068"
            )
            return
        await conn.execute(SQL_UP)
        logger.info(
            "Rewrote content_tasks_update_redirect() to propagate "
            "qa_feedback + category (GH-86)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_proc WHERE proname = 'content_tasks_update_redirect'"
        )
        if not exists:
            return
        await conn.execute(SQL_DOWN)
        logger.info("Reverted content_tasks_update_redirect() to pre-GH-86 state")
