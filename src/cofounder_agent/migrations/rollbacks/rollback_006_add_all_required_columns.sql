-- Rollback: Add All Required Columns to content_tasks
-- Reverses: 006_add_all_required_columns.sql
-- WARNING: Drops all these columns and their data

BEGIN;

ALTER TABLE content_tasks
    DROP COLUMN IF EXISTS topic,
    DROP COLUMN IF EXISTS style,
    DROP COLUMN IF EXISTS tone,
    DROP COLUMN IF EXISTS target_length,
    DROP COLUMN IF EXISTS primary_keyword,
    DROP COLUMN IF EXISTS target_audience,
    DROP COLUMN IF EXISTS category,
    DROP COLUMN IF EXISTS writing_style_id,
    DROP COLUMN IF EXISTS content,
    DROP COLUMN IF EXISTS excerpt,
    DROP COLUMN IF EXISTS featured_image_url,
    DROP COLUMN IF EXISTS featured_image_data,
    DROP COLUMN IF EXISTS featured_image_prompt,
    DROP COLUMN IF EXISTS qa_feedback,
    DROP COLUMN IF EXISTS quality_score,
    DROP COLUMN IF EXISTS seo_title,
    DROP COLUMN IF EXISTS seo_description,
    DROP COLUMN IF EXISTS seo_keywords,
    DROP COLUMN IF EXISTS percentage,
    DROP COLUMN IF EXISTS message,
    DROP COLUMN IF EXISTS model_used,
    DROP COLUMN IF EXISTS approval_status,
    DROP COLUMN IF EXISTS publish_mode,
    DROP COLUMN IF EXISTS model_selections,
    DROP COLUMN IF EXISTS quality_preference,
    DROP COLUMN IF EXISTS estimated_cost,
    DROP COLUMN IF EXISTS actual_cost,
    DROP COLUMN IF EXISTS cost_breakdown,
    DROP COLUMN IF EXISTS agent_id,
    DROP COLUMN IF EXISTS started_at,
    DROP COLUMN IF EXISTS published_at,
    DROP COLUMN IF EXISTS human_feedback,
    DROP COLUMN IF EXISTS approved_by,
    DROP COLUMN IF EXISTS approval_timestamp,
    DROP COLUMN IF EXISTS approval_notes,
    DROP COLUMN IF EXISTS progress,
    DROP COLUMN IF EXISTS tags,
    DROP COLUMN IF EXISTS task_metadata,
    DROP COLUMN IF EXISTS error_message;

COMMIT;
