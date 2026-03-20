-- Rollback: Add Writing Style ID Column
-- Reverses: 005_add_writing_style_id.sql

BEGIN;

DROP INDEX IF EXISTS idx_content_tasks_writing_style_id;

ALTER TABLE content_tasks
    DROP CONSTRAINT IF EXISTS fk_writing_style_id,
    DROP COLUMN IF EXISTS writing_style_id;

COMMIT;
