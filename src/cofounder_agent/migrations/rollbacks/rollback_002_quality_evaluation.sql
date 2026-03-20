-- Rollback: Quality Evaluation Tables
-- Reverses: 002_quality_evaluation.sql
-- WARNING: Destroys all quality evaluation data

BEGIN;

DROP INDEX IF EXISTS idx_quality_evaluations_content_id;
DROP INDEX IF EXISTS idx_quality_evaluations_task_id;
DROP INDEX IF EXISTS idx_quality_evaluations_passing;
DROP INDEX IF EXISTS idx_quality_evaluations_overall_score;
DROP TABLE IF EXISTS quality_evaluations;

COMMIT;
