-- Migration: Add title column to content_tasks table
-- Date: 2026-01-23
-- Purpose: Support LLM-generated blog post titles

-- Local Development Database
-- Add title column to content_tasks if it doesn't exist
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS title VARCHAR(500);

-- Create index for faster lookups by title
CREATE INDEX IF NOT EXISTS idx_content_tasks_title ON content_tasks(title);

-- Update existing tasks with NULL titles (set to topic as fallback)
UPDATE content_tasks SET title = topic WHERE title IS NULL;

-- Output verification
SELECT 
    COUNT(*) as total_tasks,
    COUNT(CASE WHEN title IS NOT NULL THEN 1 END) as tasks_with_title,
    COUNT(CASE WHEN title IS NULL THEN 1 END) as tasks_with_null_title
FROM content_tasks;
