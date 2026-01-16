-- Migration: Create task_status_history table for status change auditing
-- Created: 2025-12-22
-- Purpose: Log all task status changes for audit trail and compliance

-- Drop existing table if it exists (for development/testing)
-- In production, use careful migration approach
DROP TABLE IF EXISTS task_status_history CASCADE;

-- Create task_status_history table
CREATE TABLE task_status_history (
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
        ON DELETE CASCADE,
    
    -- Indexes for query performance
    INDEX idx_task_status_history_task_id (task_id),
    INDEX idx_task_status_history_timestamp (timestamp),
    INDEX idx_task_status_history_new_status (new_status),
    INDEX idx_task_status_history_task_timestamp (task_id, timestamp DESC)
);

-- Create indexes
CREATE INDEX idx_task_status_history_task_id ON task_status_history(task_id);
CREATE INDEX idx_task_status_history_timestamp ON task_status_history(timestamp DESC);
CREATE INDEX idx_task_status_history_new_status ON task_status_history(new_status);
CREATE INDEX idx_task_status_history_task_timestamp ON task_status_history(task_id, timestamp DESC);

-- Add comment for documentation
COMMENT ON TABLE task_status_history IS 'Audit trail for all task status changes - includes validation failures, state transitions, and change reasons';
COMMENT ON COLUMN task_status_history.metadata IS 'Additional context: validation errors, user ID, request details, etc.';
