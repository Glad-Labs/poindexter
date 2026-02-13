"""
Database migration: Create workflow_executions table for storing workflow execution results

This migration adds support for tracking workflow execution history,
including execution status, phase results, and performance metrics.
"""

async def up(pool):
    """Create workflow_executions table"""
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_executions (
            id UUID PRIMARY KEY,
            workflow_id UUID NOT NULL,
            owner_id VARCHAR(255) NOT NULL,
            execution_status VARCHAR(50) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            duration_ms INTEGER,
            
            initial_input JSONB,
            phase_results JSONB DEFAULT '{}'::jsonb,
            final_output JSONB,
            error_message TEXT,
            
            progress_percent INTEGER DEFAULT 0,
            completed_phases INTEGER DEFAULT 0,
            total_phases INTEGER DEFAULT 0,
            
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb,
            
            CONSTRAINT fk_workflow_executions_workflow 
                FOREIGN KEY(workflow_id) 
                REFERENCES custom_workflows(id) 
                ON DELETE CASCADE
        );
        """
    )

    # Create indexes for common queries
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id 
        ON workflow_executions(workflow_id);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_owner_id 
        ON workflow_executions(owner_id);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_status
        ON workflow_executions(execution_status);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_created_at
        ON workflow_executions(created_at DESC);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_owner
        ON workflow_executions(workflow_id, owner_id);
        """
    )


async def down(pool):
    """Drop workflow_executions table"""
    await pool.execute("DROP TABLE IF EXISTS workflow_executions CASCADE;")
