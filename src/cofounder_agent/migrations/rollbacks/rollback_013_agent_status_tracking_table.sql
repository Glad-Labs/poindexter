-- Rollback: Agent Status Tracking Table
-- Reverses: 013_agent_status_tracking_table.sql
-- WARNING: Destroys all agent status tracking data

BEGIN;

DROP INDEX IF EXISTS idx_agent_status_agent_name;
DROP INDEX IF EXISTS idx_agent_status_status;
DROP INDEX IF EXISTS idx_agent_status_last_run;
DROP TABLE IF EXISTS agent_status;

COMMIT;
