-- Migration: Create task_status_history table for status change auditing
-- Created: 2025-12-22
-- Purpose: Log all task status changes for audit trail and compliance

-- Create task_status_history table (idempotent — safe to run on existing databases)
CREATE TABLE IF NOT EXISTS task_status_history (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(255) NOT NULL,
    old_status VARCHAR(50) NOT NULL,
    new_status VARCHAR(50) NOT NULL,
    reason TEXT,
    metadata JSONB DEFAULT '{}',
    timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Foreign key to content_tasks
    CONSTRAINT fk_task_status_history_task_id 
        FOREIGN KEY (task_id) 
        REFERENCES content_tasks(task_id) 
        ON DELETE CASCADE
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_task_status_history_task_id ON task_status_history(task_id);
CREATE INDEX IF NOT EXISTS idx_task_status_history_timestamp ON task_status_history(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_task_status_history_new_status ON task_status_history(new_status);
CREATE INDEX IF NOT EXISTS idx_task_status_history_task_timestamp ON task_status_history(task_id, timestamp DESC);

-- Add comment for documentation
COMMENT ON TABLE task_status_history IS 'Audit trail for all task status changes - includes validation failures, state transitions, and change reasons';
COMMENT ON COLUMN task_status_history.metadata IS 'Additional context: validation errors, user ID, request details, etc.';
