-- Migration: Make content_tasks.id non-nullable with a UUID default
-- Fixes issue #301 — content_tasks.id is nullable UUID with no DEFAULT.
-- Multiple NULL rows bypass the UNIQUE constraint and the numeric lookup path
-- (int(task_id) → UUID column) always raised DataError.
-- The application-level fix (removing the numeric fallback) is in tasks_db.py.
-- This migration ensures the id column is properly populated.
-- Version: 019

BEGIN;

-- Set a default so new rows always get a UUID
ALTER TABLE content_tasks ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- Backfill any existing NULL rows
UPDATE content_tasks SET id = gen_random_uuid() WHERE id IS NULL;

-- Now enforce NOT NULL
ALTER TABLE content_tasks ALTER COLUMN id SET NOT NULL;

COMMIT;
