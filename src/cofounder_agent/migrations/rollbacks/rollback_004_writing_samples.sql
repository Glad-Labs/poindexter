-- Rollback: Writing Samples Table
-- Reverses: 004_writing_samples.sql
-- WARNING: Destroys all writing samples

BEGIN;

DROP INDEX IF EXISTS idx_unique_active_per_user;
DROP INDEX IF EXISTS idx_writing_samples_user_id;
DROP INDEX IF EXISTS idx_writing_samples_created_at;
DROP TABLE IF EXISTS writing_samples;

COMMIT;
