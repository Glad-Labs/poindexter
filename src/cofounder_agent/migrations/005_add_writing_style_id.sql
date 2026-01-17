-- Migration: Add writing_style_id to content_tasks table
-- Purpose: Enable tasks to be associated with user writing samples for style guidance
-- Date: January 8, 2026
-- Status: WIP - Part of Phase 2 Writing Style System

BEGIN;

-- Add writing_style_id column to content_tasks table if not exists
ALTER TABLE content_tasks 
ADD COLUMN IF NOT EXISTS writing_style_id INTEGER DEFAULT NULL;

-- Add foreign key constraint if it doesn't exist
-- Using IF NOT EXISTS pattern with constraint check
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE constraint_name = 'fk_writing_style_id'
    ) THEN
        ALTER TABLE content_tasks
        ADD CONSTRAINT fk_writing_style_id 
            FOREIGN KEY (writing_style_id) 
            REFERENCES writing_samples(id) 
            ON DELETE SET NULL;
    END IF;
END $$;

-- Create index for faster lookups by writing_style_id
CREATE INDEX IF NOT EXISTS idx_content_tasks_writing_style_id 
ON content_tasks(writing_style_id);

COMMIT;

-- Log migration completion
SELECT 'Migration 005: writing_style_id column added to content_tasks' as status;
