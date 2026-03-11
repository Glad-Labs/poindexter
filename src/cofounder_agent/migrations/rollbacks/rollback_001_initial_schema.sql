-- Rollback: Initial Schema
-- Reverses: 001_initial_schema.sql
-- WARNING: Destroys all task and content_task data

BEGIN;

-- Drop indexes first
DROP INDEX IF EXISTS idx_tasks_status;
DROP INDEX IF EXISTS idx_tasks_task_id;
DROP INDEX IF EXISTS idx_tasks_created_at;
DROP INDEX IF EXISTS idx_tasks_type;
DROP INDEX IF EXISTS idx_content_tasks_status;
DROP INDEX IF EXISTS idx_content_tasks_task_id;
DROP INDEX IF EXISTS idx_content_tasks_content_type;
DROP INDEX IF EXISTS idx_content_tasks_created_at;

-- Drop tables
DROP TABLE IF EXISTS content_tasks;
DROP TABLE IF EXISTS tasks;

COMMIT;
