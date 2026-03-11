-- Rollback: Add Missing Task Columns
-- Reverses: 001_add_missing_task_columns.sql

BEGIN;

ALTER TABLE content_tasks
    DROP COLUMN IF EXISTS task_type,
    DROP COLUMN IF EXISTS request_type;

COMMIT;
