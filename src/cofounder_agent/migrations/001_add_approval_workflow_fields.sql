-- Migration: Add Approval Workflow Fields to content_tasks
-- Date: 2025-11-14
-- Description: Add approval_status, qa_feedback, human_feedback, approved_by, 
--              approval_timestamp, and approval_notes columns to content_tasks table
--              for Phase 5 human approval gate implementation

BEGIN;

-- Add approval workflow columns if they don't exist
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'pending' NOT NULL,
ADD COLUMN IF NOT EXISTS qa_feedback TEXT,
ADD COLUMN IF NOT EXISTS human_feedback TEXT,
ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255),
ADD COLUMN IF NOT EXISTS approval_timestamp TIMESTAMP,
ADD COLUMN IF NOT EXISTS approval_notes TEXT;

-- Create index on approval_status for faster filtering
CREATE INDEX IF NOT EXISTS idx_content_tasks_approval_status 
ON content_tasks(approval_status);

-- Create index on approved_by for filtering by reviewer
CREATE INDEX IF NOT EXISTS idx_content_tasks_approved_by 
ON content_tasks(approved_by);

-- Create composite index for common queries
CREATE INDEX IF NOT EXISTS idx_content_tasks_status_approval 
ON content_tasks(status, approval_status);

COMMIT;
