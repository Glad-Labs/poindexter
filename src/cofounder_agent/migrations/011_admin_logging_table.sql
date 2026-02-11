-- Migration: Create logs table for administrative logging
-- Version: 011
-- Purpose: Store administrative logs (agent activity, warnings, errors, etc.)
-- This table supports the admin_db.add_log_entry() and get_logs() methods

CREATE TABLE IF NOT EXISTS logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name VARCHAR(255) NOT NULL,
    level VARCHAR(20) NOT NULL,          -- DEBUG, INFO, WARNING, ERROR, CRITICAL
    message TEXT NOT NULL,
    context JSONB,                       -- Optional context/metadata dict
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_logs_agent_name ON logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);

-- Composite index for filtering by agent and date
CREATE INDEX IF NOT EXISTS idx_logs_agent_date ON logs(agent_name, created_at);

-- Comment on table
COMMENT ON TABLE logs IS 'Administrative logging for agent activity and system events';
COMMENT ON COLUMN logs.level IS 'Log severity level: DEBUG, INFO, WARNING, ERROR, CRITICAL';
