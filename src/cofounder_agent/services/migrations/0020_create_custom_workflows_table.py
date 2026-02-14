"""
Database migration: Create custom_workflows table for user-defined workflow templates

This migration adds support for storing and retrieving custom workflows
that users create in the workflow builder.
"""

async def up(pool):
    """Create custom_workflows table"""
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_workflows (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT NOT NULL,
            phases JSONB NOT NULL,
            owner_id VARCHAR(255) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            tags JSONB DEFAULT '[]'::jsonb,
            is_template BOOLEAN DEFAULT FALSE,
            
            CONSTRAINT custom_workflows_name_owner_unique UNIQUE(name, owner_id)
        );
        """
    )

    # Create indexes for common queries
    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_custom_workflows_owner_id 
        ON custom_workflows(owner_id);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_custom_workflows_is_template
        ON custom_workflows(is_template);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_custom_workflows_created_at
        ON custom_workflows(created_at DESC);
        """
    )

    await pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_custom_workflows_updated_at
        ON custom_workflows(updated_at DESC);
        """
    )


async def down(pool):
    """Drop custom_workflows table"""
    await pool.execute("DROP TABLE IF EXISTS custom_workflows CASCADE;")
