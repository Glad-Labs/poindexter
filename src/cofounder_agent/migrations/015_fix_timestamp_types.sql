-- Migration: Standardise timestamp types and add updated_at default
-- Fixes issues:
--   #246 — content_tasks mixes TIMESTAMP WITH/WITHOUT TIME ZONE; updated_at lacks NOT NULL/DEFAULT
--   #309 — task_status_history uses TIMESTAMP WITHOUT TIME ZONE and reserved column name 'timestamp'
-- Version: 015
-- NOTE: Run a data-cleanup pass and verify no active transactions before applying.
--       Use CONCURRENTLY where possible for production deployments.

BEGIN;

-- --------------------------------------------------------------------
-- Fix content_tasks: standardise all timestamp columns to TIMESTAMPTZ
-- and add NOT NULL + DEFAULT to updated_at (#246)
-- --------------------------------------------------------------------
ALTER TABLE content_tasks
  ALTER COLUMN created_at         TYPE TIMESTAMP WITH TIME ZONE
    USING created_at AT TIME ZONE 'UTC',
  ALTER COLUMN updated_at         TYPE TIMESTAMP WITH TIME ZONE
    USING COALESCE(updated_at, NOW()) AT TIME ZONE 'UTC',
  ALTER COLUMN updated_at         SET NOT NULL,
  ALTER COLUMN updated_at         SET DEFAULT NOW(),
  ALTER COLUMN completed_at       TYPE TIMESTAMP WITH TIME ZONE
    USING completed_at AT TIME ZONE 'UTC',
  ALTER COLUMN approval_timestamp TYPE TIMESTAMP WITH TIME ZONE
    USING approval_timestamp AT TIME ZONE 'UTC';

-- --------------------------------------------------------------------
-- Fix task_status_history: rename 'timestamp' (reserved word) to
-- created_at and convert to TIMESTAMPTZ (#309)
-- --------------------------------------------------------------------
ALTER TABLE task_status_history
  RENAME COLUMN "timestamp" TO created_at;

ALTER TABLE task_status_history
  ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE
    USING created_at AT TIME ZONE 'UTC';

ALTER TABLE task_status_history
  ALTER COLUMN created_at SET DEFAULT NOW();

-- Update the index that referenced the old column name
DROP INDEX IF EXISTS idx_task_status_history_timestamp;
CREATE INDEX IF NOT EXISTS idx_task_status_history_created_at
  ON task_status_history(created_at DESC);

COMMIT;
