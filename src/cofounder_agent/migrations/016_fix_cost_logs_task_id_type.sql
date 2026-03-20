-- Migration: Align cost_logs.task_id type with content_tasks.task_id
-- Fixes issue #250 Bug 1 — cost_logs.task_id is UUID but content_tasks.task_id is VARCHAR(255),
-- causing an implicit cast on every JOIN and preventing index use.
-- Changing cost_logs.task_id to VARCHAR(255) to match the FK source column.
-- Version: 016
-- NOTE: Verify no non-UUID values exist in cost_logs.task_id before applying.

BEGIN;

-- Drop the index before altering the type
DROP INDEX IF EXISTS idx_cost_logs_task_id;
DROP INDEX IF EXISTS idx_cost_logs_task_phase;

-- Change task_id from UUID to VARCHAR(255) to match content_tasks.task_id
ALTER TABLE cost_logs
  ALTER COLUMN task_id TYPE VARCHAR(255) USING task_id::text;

-- Recreate the indexes on the new type
CREATE INDEX IF NOT EXISTS idx_cost_logs_task_id ON cost_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_cost_logs_task_phase ON cost_logs(task_id, phase);

COMMIT;
