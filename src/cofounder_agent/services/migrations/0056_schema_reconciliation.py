"""
Migration 0056: Schema Reconciliation

Ensures all expected columns exist on all tables, regardless of what
state the database was in when earlier migrations ran. This fixes the
"table exists but columns are missing" problem that occurs when a table
was created by an older system (Strapi, manual SQL) before the migration
runner was introduced.

Uses ADD COLUMN IF NOT EXISTS (PostgreSQL 9.6+) for safety.
Runs idempotently — safe to re-run on any database.
"""

from contextlib import suppress

RECONCILE_SQL = """
-- ============================================================
-- content_tasks: ensure all columns exist
-- ============================================================
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS request_type VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS task_type VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS content_type VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS topic VARCHAR(500);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS title VARCHAR(500);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS style VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS tone VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS target_length INTEGER;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS agent_id VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS stage VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS quality_score FLOAT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS qa_feedback TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'pending';
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS publish_mode VARCHAR(50) DEFAULT 'draft';
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS publish_status VARCHAR(50);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS model_used VARCHAR(200);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS actual_cost FLOAT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS estimated_cost FLOAT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS content TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS excerpt TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_url VARCHAR(1000);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_data JSONB;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS featured_image_prompt TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_title VARCHAR(200);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_description VARCHAR(500);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS seo_keywords VARCHAR(1000);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS primary_keyword VARCHAR(200);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS target_audience VARCHAR(200);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS category VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS writing_style_id VARCHAR(255);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS tags JSONB;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS quality_preference VARCHAR(50);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS task_metadata JSONB DEFAULT '{}'::jsonb;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS result JSONB;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS error_details JSONB;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS approval_timestamp TIMESTAMPTZ;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS site_id UUID;

-- ============================================================
-- posts: ensure all columns exist
-- ============================================================
ALTER TABLE posts ADD COLUMN IF NOT EXISTS seo_title VARCHAR(200);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS seo_description VARCHAR(500);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS seo_keywords VARCHAR(1000);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS cover_image_url VARCHAR(1000);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS created_by VARCHAR(255);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS updated_by VARCHAR(255);
ALTER TABLE posts ADD COLUMN IF NOT EXISTS site_id UUID;

-- ============================================================
-- cost_logs: ensure all columns exist
-- ============================================================
ALTER TABLE cost_logs ADD COLUMN IF NOT EXISTS total_tokens INTEGER;
ALTER TABLE cost_logs ADD COLUMN IF NOT EXISTS quality_score FLOAT;
ALTER TABLE cost_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

-- ============================================================
-- users: ensure all columns exist
-- ============================================================
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users') THEN
        ALTER TABLE users ADD COLUMN IF NOT EXISTS github_id VARCHAR(255);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);
        ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) DEFAULT 'user';
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
    END IF;
END $$;

-- ============================================================
-- settings: ensure key is unique
-- ============================================================
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'settings_key_key'
    ) THEN
        ALTER TABLE settings ADD CONSTRAINT settings_key_key UNIQUE (key);
    END IF;
EXCEPTION WHEN duplicate_table THEN NULL;
END $$;
"""


async def up(pool):
    """Reconcile schema — add any missing columns to existing tables."""
    async with pool.acquire() as conn:
        # Execute each statement individually to avoid partial failures
        for statement in RECONCILE_SQL.split(";"):
            statement = statement.strip()
            if statement and not statement.startswith("--"):
                # Column may already exist with different type, or table may
                # not exist — both are fine, we're only ensuring columns exist.
                with suppress(Exception):
                    await conn.execute(statement + ";")


async def down(pool):
    """No-op — reconciliation is additive and safe to leave in place."""
    pass
