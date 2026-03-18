"""
Migration 0051: Add missing content_tasks and task_status_history columns

Backfills columns from raw SQL migrations 005, 006, 009, and 001 that were
not included in the Python base schema (0000). Uses ADD COLUMN IF NOT EXISTS
so it is safe on databases that already have these columns from the raw SQL
migration path.

Closes #1015, #1013
"""


async def up(pool):
    """Add missing columns to content_tasks and task_status_history."""
    statements = [
        # -- content_tasks columns from 006_add_all_required_columns.sql --
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS topic VARCHAR(500)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS style VARCHAR(100) DEFAULT 'technical'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS tone VARCHAR(100) DEFAULT 'professional'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS target_length INTEGER DEFAULT 1500",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS primary_keyword VARCHAR(255)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS target_audience VARCHAR(255)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS category VARCHAR(100)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS writing_style_id INTEGER",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS content TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS excerpt TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_url VARCHAR(500)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_data JSONB",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_prompt TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS qa_feedback TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS quality_score INTEGER",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_title VARCHAR(255)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_description VARCHAR(500)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_keywords VARCHAR(500)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS percentage INTEGER DEFAULT 0",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS message TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS model_used VARCHAR(255)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'pending'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS publish_mode VARCHAR(50) DEFAULT 'draft'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS model_selections JSONB DEFAULT '{}'::jsonb",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS quality_preference VARCHAR(50) DEFAULT 'balanced'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(10,4) DEFAULT 0.0000",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(10,4) DEFAULT 0.0000",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS cost_breakdown JSONB",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS agent_id VARCHAR(100) DEFAULT 'content-agent'",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS published_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS human_feedback TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255)",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approval_timestamp TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approval_notes TEXT",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS progress JSONB DEFAULT '{}'::jsonb",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS task_metadata JSONB DEFAULT '{}'::jsonb",
        # -- content_tasks columns from 009_add_multi_phase_model_tracking.sql --
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS models_used_by_phase JSONB DEFAULT '{}'::jsonb",
        "ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS model_selection_log JSONB DEFAULT '{}'::jsonb",
        # -- content_tasks indexes --
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_topic ON content_tasks(topic)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_category ON content_tasks(category)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_approval_status ON content_tasks(approval_status)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_publish_mode ON content_tasks(publish_mode)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_quality_preference ON content_tasks(quality_preference)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_writing_style_id ON content_tasks(writing_style_id)",
        "CREATE INDEX IF NOT EXISTS idx_content_tasks_model_used ON content_tasks(model_used)",
        # -- task_status_history columns from 001_create_task_status_history.sql --
        "ALTER TABLE task_status_history ADD COLUMN IF NOT EXISTS reason TEXT",
        "ALTER TABLE task_status_history ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb",
    ]

    async with pool.acquire() as conn:
        for stmt in statements:
            await conn.execute(stmt)


async def down(pool):
    """Rollback is a no-op — dropping columns risks data loss."""
    pass
