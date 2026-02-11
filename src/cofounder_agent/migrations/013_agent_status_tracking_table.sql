-- Migration: Create agent_status table for agent health monitoring
-- Version: 013
-- Purpose: Track agent status and heartbeat for system health monitoring
-- This table supports the admin_db.update_agent_status() and get_agent_status() methods

CREATE TABLE IF NOT EXISTS agent_status (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL,         -- active, idle, error, maintenance, etc.
    last_run TIMESTAMP WITH TIME ZONE,
    metadata JSONB,                      -- Optional metadata dict
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_agent_status_agent_name ON agent_status(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_status_status ON agent_status(status);
CREATE INDEX IF NOT EXISTS idx_agent_status_last_run ON agent_status(last_run);

-- Comment on table
COMMENT ON TABLE agent_status IS 'Agent health status and heartbeat tracking for monitoring';
COMMENT ON COLUMN agent_status.status IS 'Current agent state: active, idle, error, maintenance, etc.';
COMMENT ON COLUMN agent_status.metadata IS 'JSON object with additional agent state information';
