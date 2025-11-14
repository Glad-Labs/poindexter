-- Migration: Add task_type column to content_tasks table
-- Date: 2025-11-13
-- Purpose: Fix schema mismatch - add missing task_type column

ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS task_type VARCHAR(50) DEFAULT 'blog_post' NOT NULL;

CREATE INDEX IF NOT EXISTS idx_content_tasks_task_type ON content_tasks(task_type);
