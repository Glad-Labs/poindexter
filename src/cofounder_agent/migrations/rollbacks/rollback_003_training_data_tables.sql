-- Rollback: Training Data Tables
-- Reverses: 003_training_data_tables.sql
-- WARNING: Destroys all training data

BEGIN;

DROP INDEX IF EXISTS idx_orchestrator_training_data_execution_id;
DROP INDEX IF EXISTS idx_orchestrator_training_data_quality_score;
DROP INDEX IF EXISTS idx_orchestrator_training_data_intent;
DROP INDEX IF EXISTS idx_orchestrator_training_data_success;
DROP TABLE IF EXISTS orchestrator_training_data;

COMMIT;
