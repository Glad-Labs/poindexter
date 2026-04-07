"""Make cost_logs.task_id nullable + add cost_type column for electricity/idle tracking."""

SQL_UP = """
-- Allow NULL task_id for system-level costs (electricity, idle)
ALTER TABLE cost_logs ALTER COLUMN task_id DROP NOT NULL;

-- Add cost_type to distinguish LLM inference from electricity/idle costs
DO $$ BEGIN
    ALTER TABLE cost_logs ADD COLUMN cost_type VARCHAR(30) DEFAULT 'inference';
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

-- Index for cost_type queries
CREATE INDEX IF NOT EXISTS idx_cost_logs_cost_type ON cost_logs (cost_type);
"""

SQL_DOWN = """
ALTER TABLE cost_logs ALTER COLUMN task_id SET NOT NULL;
ALTER TABLE cost_logs DROP COLUMN IF EXISTS cost_type;
DROP INDEX IF EXISTS idx_cost_logs_cost_type;
"""
