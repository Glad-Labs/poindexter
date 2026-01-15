-- Migration: Add missing columns to content_tasks table
-- Date: 2026-01-14
-- Purpose: Add task_type and request_type columns expected by tasks_db.py insert operations

BEGIN;

-- Add task_type column if it doesn't exist
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS task_type VARCHAR(50);

-- Add request_type column if it doesn't exist  
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS request_type VARCHAR(50);

-- Update any NULL values to defaults
UPDATE content_tasks SET task_type = 'blog_post' WHERE task_type IS NULL;
UPDATE content_tasks SET request_type = 'content_generation' WHERE request_type IS NULL;

-- Add NOT NULL constraints and defaults (separate statements)
ALTER TABLE content_tasks
ALTER COLUMN task_type SET NOT NULL;

ALTER TABLE content_tasks
ALTER COLUMN task_type SET DEFAULT 'blog_post';

ALTER TABLE content_tasks
ALTER COLUMN request_type SET NOT NULL;

ALTER TABLE content_tasks
ALTER COLUMN request_type SET DEFAULT 'content_generation';

-- Create index on task_type for faster queries
CREATE INDEX IF NOT EXISTS idx_content_tasks_task_type ON content_tasks(task_type);

-- Create index on request_type for faster queries
CREATE INDEX IF NOT EXISTS idx_content_tasks_request_type ON content_tasks(request_type);

COMMIT;
