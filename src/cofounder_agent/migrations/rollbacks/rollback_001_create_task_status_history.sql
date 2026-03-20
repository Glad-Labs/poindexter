-- Rollback: Task Status History Table
-- Reverses: 001_create_task_status_history.sql
-- WARNING: Destroys all task status history data

BEGIN;

DROP INDEX IF EXISTS idx_task_status_history_task_id;
DROP INDEX IF EXISTS idx_task_status_history_timestamp;
DROP INDEX IF EXISTS idx_task_status_history_new_status;
DROP INDEX IF EXISTS idx_task_status_history_task_timestamp;
DROP TABLE IF EXISTS task_status_history;

COMMIT;
