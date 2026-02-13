"""
Database migration: Create capability_tasks and capability_executions tables.

These tables store task definitions and execution results for capability-based
task composition system (different from workflow_executions).
"""

async def up(pool):
    """Create capability_tasks and capability_executions tables."""
    
    # Create capability_tasks table
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS capability_tasks (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            owner_id VARCHAR(255) NOT NULL,
            
            -- Task definition
            steps JSONB NOT NULL,  -- Array of steps
            tags JSONB DEFAULT '[]'::jsonb,  -- Tagging for organization
            
            -- Metadata
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            
            -- Status and versioning
            is_active BOOLEAN DEFAULT TRUE,
            version INTEGER DEFAULT 1,
            
            -- Cost and performance tracking
            estimated_cost_cents INTEGER,
            avg_duration_ms FLOAT,
            
            -- Metrics
            execution_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_executed_at TIMESTAMP WITH TIME ZONE
        );
        """
    )
    
    # Create capability_executions table
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS capability_executions (
            id VARCHAR(36) PRIMARY KEY,
            task_id VARCHAR(36) NOT NULL,
            owner_id VARCHAR(255) NOT NULL,
            
            -- Execution status
            status VARCHAR(50) NOT NULL,  -- pending, running, completed, failed
            error_message TEXT,
            
            -- Execution results
            step_results JSONB DEFAULT '[]'::jsonb,  -- Array of step results
            final_outputs JSONB DEFAULT '{}'::jsonb,  -- Final step outputs
            
            -- Performance metrics
            total_duration_ms FLOAT,
            progress_percent INTEGER DEFAULT 0,
            completed_steps INTEGER DEFAULT 0,
            total_steps INTEGER NOT NULL,
            
            -- Cost tracking (if any billable steps)
            cost_cents INTEGER,
            
            -- Timestamps
            started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            
            -- Metadata
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb,
            
            -- Foreign key
            CONSTRAINT fk_capability_executions_task
                FOREIGN KEY(task_id) 
                REFERENCES capability_tasks(id) 
                ON DELETE CASCADE
        );
        """
    )
    
    # Create indexes for performance
    
    # capability_tasks indexes
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_tasks_owner_id ON capability_tasks(owner_id);")
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_tasks_created_at ON capability_tasks(created_at);")
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_tasks_is_active ON capability_tasks(is_active);")
    
    # capability_executions indexes
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_executions_task_id ON capability_executions(task_id);")
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_executions_owner_id ON capability_executions(owner_id);")
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_executions_status ON capability_executions(status);")
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_executions_started_at ON capability_executions(started_at);")
    
    # Composite index for common query pattern
    await pool.execute("CREATE INDEX IF NOT EXISTS ix_capability_executions_owner_task ON capability_executions(owner_id, task_id);")


async def down(pool):
    """Drop capability_tasks and capability_executions tables."""
    
    # Drop tables ( CASCADE handles indexes and constraints)
    await pool.execute("DROP TABLE IF EXISTS capability_executions CASCADE;")
    await pool.execute("DROP TABLE IF EXISTS capability_tasks CASCADE;")

