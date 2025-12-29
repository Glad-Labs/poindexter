-- Migration: Create cost_logs table for per-API-call cost tracking
-- Version: 002
-- Purpose: Track costs of each LLM API call by phase and model
-- This enables transparent cost breakdown and budget management

-- Create cost_logs table
CREATE TABLE IF NOT EXISTS cost_logs (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    user_id UUID,
    phase VARCHAR(50) NOT NULL,          -- research, outline, draft, assess, refine, finalize
    model VARCHAR(100) NOT NULL,         -- ollama, gpt-3.5-turbo, gpt-4, claude-3-opus, etc.
    provider VARCHAR(50) NOT NULL,       -- ollama, openai, anthropic, google
    
    -- Token tracking
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,
    
    -- Cost tracking
    cost_usd DECIMAL(10, 6),             -- Cost in USD ($0.000001 precision)
    
    -- Metadata
    quality_score FLOAT,                 -- Optional: 1-5 star rating
    duration_ms INT,                     -- Execution time in milliseconds
    success BOOLEAN DEFAULT TRUE,        -- Whether call succeeded
    error_message TEXT,                  -- Error details if failed
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX idx_cost_logs_task_id ON cost_logs(task_id);
CREATE INDEX idx_cost_logs_user_id ON cost_logs(user_id);
CREATE INDEX idx_cost_logs_created_at ON cost_logs(created_at);
CREATE INDEX idx_cost_logs_provider ON cost_logs(provider);
CREATE INDEX idx_cost_logs_model ON cost_logs(model);
CREATE INDEX idx_cost_logs_phase ON cost_logs(phase);

-- Composite index for cost aggregation queries (for dashboard)
CREATE INDEX idx_cost_logs_user_date ON cost_logs(user_id, created_at);

-- Composite index for task cost breakdown
CREATE INDEX idx_cost_logs_task_phase ON cost_logs(task_id, phase);

-- Comment on table
COMMENT ON TABLE cost_logs IS 'Per-API-call cost tracking for transparency and budget management';
COMMENT ON COLUMN cost_logs.phase IS 'Pipeline phase: research, outline, draft, assess, refine, finalize';
COMMENT ON COLUMN cost_logs.provider IS 'LLM provider: ollama, openai, anthropic, google';
COMMENT ON COLUMN cost_logs.cost_usd IS 'Cost in USD with 6 decimal places ($0.000001 precision)';
