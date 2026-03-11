-- Rollback: Admin Logging Table
-- Reverses: 011_admin_logging_table.sql
-- WARNING: Destroys all admin log data

BEGIN;

DROP INDEX IF EXISTS idx_logs_agent_name;
DROP INDEX IF EXISTS idx_logs_level;
DROP INDEX IF EXISTS idx_logs_created_at;
DROP INDEX IF EXISTS idx_logs_agent_date;
DROP TABLE IF EXISTS logs;

COMMIT;
