-- Rollback: Cost Logs Table
-- Reverses: 002a_cost_logs_table.sql
-- WARNING: Destroys all cost log data

BEGIN;

DROP INDEX IF EXISTS idx_cost_logs_task_id;
DROP INDEX IF EXISTS idx_cost_logs_user_id;
DROP INDEX IF EXISTS idx_cost_logs_created_at;
DROP INDEX IF EXISTS idx_cost_logs_provider;
DROP TABLE IF EXISTS cost_logs;

COMMIT;
