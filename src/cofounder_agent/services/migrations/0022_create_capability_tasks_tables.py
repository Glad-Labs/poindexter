"""
Alembic migration: Create capability_tasks and capability_executions tables.

These tables store task definitions and execution results for capability-based
task composition system (different from workflow_executions).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    """Create capability_tasks and capability_executions tables."""
    
    # Create capability_tasks table
    op.create_table(
        'capability_tasks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('owner_id', sa.String(255), nullable=False),
        
        # Task definition
        sa.Column('steps', postgresql.JSONB, nullable=False),  # Array of steps
        sa.Column('tags', postgresql.JSONB, nullable=True),  # Tagging for organization
        
        # Metadata
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        
        # Status and versioning
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('version', sa.Integer, default=1),
        
        # Cost and performance tracking
        sa.Column('estimated_cost_cents', sa.Integer, nullable=True),
        sa.Column('avg_duration_ms', sa.Float, nullable=True),
        
        # Metrics
        sa.Column('execution_count', sa.Integer, default=0),
        sa.Column('success_count', sa.Integer, default=0),
        sa.Column('failure_count', sa.Integer, default=0),
        sa.Column('last_executed_at', sa.DateTime, nullable=True),
    )
    
    # Create capability_executions table
    op.create_table(
        'capability_executions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), nullable=False),
        sa.Column('owner_id', sa.String(255), nullable=False),
        
        # Execution status
        sa.Column('status', sa.String(50), nullable=False),  # pending, running, completed, failed
        sa.Column('error_message', sa.Text, nullable=True),
        
        # Execution results
        sa.Column('step_results', postgresql.JSONB, nullable=True),  # Array of step results
        sa.Column('final_outputs', postgresql.JSONB, nullable=True),  # Final step outputs
        
        # Performance metrics
        sa.Column('total_duration_ms', sa.Float, nullable=True),
        sa.Column('progress_percent', sa.Integer, default=0),
        sa.Column('completed_steps', sa.Integer, default=0),
        sa.Column('total_steps', sa.Integer, nullable=False),
        
        # Cost tracking (if any billable steps)
        sa.Column('cost_cents', sa.Integer, nullable=True),
        
        # Timestamps
        sa.Column('started_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        
        # Metadata
        sa.Column('tags', postgresql.JSONB, nullable=True),
        sa.Column('metadata', postgresql.JSONB, nullable=True),
        
        # Foreign key
        sa.ForeignKeyConstraint(['task_id'], ['capability_tasks.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for performance
    
    # capability_tasks indexes
    op.create_index(
        'ix_capability_tasks_owner_id',
        'capability_tasks',
        ['owner_id'],
    )
    
    op.create_index(
        'ix_capability_tasks_created_at',
        'capability_tasks',
        ['created_at'],
    )
    
    op.create_index(
        'ix_capability_tasks_is_active',
        'capability_tasks',
        ['is_active'],
    )
    
    # capability_executions indexes
    op.create_index(
        'ix_capability_executions_task_id',
        'capability_executions',
        ['task_id'],
    )
    
    op.create_index(
        'ix_capability_executions_owner_id',
        'capability_executions',
        ['owner_id'],
    )
    
    op.create_index(
        'ix_capability_executions_status',
        'capability_executions',
        ['status'],
    )
    
    op.create_index(
        'ix_capability_executions_started_at',
        'capability_executions',
        ['started_at'],
    )
    
    # Composite index for common query pattern
    op.create_index(
        'ix_capability_executions_owner_task',
        'capability_executions',
        ['owner_id', 'task_id'],
    )


def downgrade():
    """Drop capability_tasks and capability_executions tables."""
    
    # Drop indexes
    op.drop_index('ix_capability_executions_owner_task', 'capability_executions')
    op.drop_index('ix_capability_executions_started_at', 'capability_executions')
    op.drop_index('ix_capability_executions_status', 'capability_executions')
    op.drop_index('ix_capability_executions_owner_id', 'capability_executions')
    op.drop_index('ix_capability_executions_task_id', 'capability_executions')
    
    op.drop_index('ix_capability_tasks_is_active', 'capability_tasks')
    op.drop_index('ix_capability_tasks_created_at', 'capability_tasks')
    op.drop_index('ix_capability_tasks_owner_id', 'capability_tasks')
    
    # Drop tables
    op.drop_table('capability_executions')
    op.drop_table('capability_tasks')
