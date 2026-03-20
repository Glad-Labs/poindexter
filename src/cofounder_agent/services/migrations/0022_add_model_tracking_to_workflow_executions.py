"""
Database migration: Add model tracking columns to workflow_executions table

This migration adds support for tracking which LLM model was selected
and whether execution used the agent pipeline or fallback mechanism.
"""


async def up(pool):
    """Add selected_model and execution_mode columns to workflow_executions table"""
    
    # Add selected_model column to track which LLM was selected
    await pool.execute(
        """
        ALTER TABLE workflow_executions
        ADD COLUMN IF NOT EXISTS selected_model VARCHAR(255);
        """
    )

    # Add execution_mode column to track "agent" vs "fallback" execution
    await pool.execute(
        """
        ALTER TABLE workflow_executions
        ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(50) DEFAULT 'agent';
        """
    )

    # Add index on selected_model for filtering/analytics queries
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_selected_model
        ON workflow_executions(selected_model);
        """
    )

    # Add index on execution_mode for tracking fallback vs agent execution
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_executions_execution_mode
        ON workflow_executions(execution_mode);
        """
    )


async def down(pool):
    """Rollback: Remove model tracking columns from workflow_executions table"""
    
    # Drop indexes
    await pool.execute(
        """
        DROP INDEX IF EXISTS idx_workflow_executions_selected_model;
        """
    )

    await pool.execute(
        """
        DROP INDEX IF EXISTS idx_workflow_executions_execution_mode;
        """
    )

    # Drop columns
    await pool.execute(
        """
        ALTER TABLE workflow_executions
        DROP COLUMN IF EXISTS selected_model;
        """
    )

    await pool.execute(
        """
        ALTER TABLE workflow_executions
        DROP COLUMN IF EXISTS execution_mode;
        """
    )
