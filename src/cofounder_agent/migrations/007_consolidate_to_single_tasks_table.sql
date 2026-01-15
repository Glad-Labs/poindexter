-- Migration: Consolidate to single content_tasks table
-- Date: 2026-01-14
-- Purpose: Remove foreign key constraint from content_tasks to make it independent
-- This simplifies task tracking by using a single unified table

BEGIN;

-- Drop the foreign key constraint if it exists
ALTER TABLE IF EXISTS content_tasks
DROP CONSTRAINT IF EXISTS content_tasks_task_id_fkey;

-- Ensure content_tasks table doesn't reference tasks anymore
-- (This allows content_tasks to work independently)

COMMIT;
