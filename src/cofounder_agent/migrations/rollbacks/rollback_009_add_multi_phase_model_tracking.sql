-- Rollback: Multi-Phase Model Tracking Columns
-- Reverses: 009_add_multi_phase_model_tracking.sql

BEGIN;

DROP INDEX IF EXISTS idx_content_tasks_model_used;
DROP INDEX IF EXISTS idx_content_tasks_gemini;

ALTER TABLE content_tasks
    DROP COLUMN IF EXISTS models_used_by_phase,
    DROP COLUMN IF EXISTS model_selection_log;

COMMIT;
