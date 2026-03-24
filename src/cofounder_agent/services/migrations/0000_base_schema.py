"""
Migration 0000: Base Schema

Creates all foundational tables that were previously managed by the old SQL
migration system (migrations/001-019). Uses CREATE TABLE IF NOT EXISTS so it
is safe to run on databases that already have these tables (e.g., production).

This migration consolidates the legacy SQL files into the new async migration
runner so that fresh databases (e.g., staging) get the complete schema on
first startup.
"""


BASE_SCHEMA_SQL = """
-- ============================================================
-- Core task tables (from 001_initial_schema.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(255) UNIQUE NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    error_message TEXT,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3
);

CREATE TABLE IF NOT EXISTS content_tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(255) UNIQUE NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    title VARCHAR(500),
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    stage VARCHAR(100) DEFAULT 'research',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    error_message TEXT,
    -- From 006_add_all_required_columns.sql
    topic VARCHAR(500),
    style VARCHAR(100) DEFAULT 'technical',
    tone VARCHAR(100) DEFAULT 'professional',
    target_length INTEGER DEFAULT 1500,
    primary_keyword VARCHAR(255),
    target_audience VARCHAR(255),
    category VARCHAR(100),
    writing_style_id INTEGER,
    content TEXT,
    excerpt TEXT,
    featured_image_url VARCHAR(500),
    featured_image_data JSONB,
    featured_image_prompt TEXT,
    qa_feedback TEXT,
    quality_score INTEGER,
    seo_title VARCHAR(255),
    seo_description VARCHAR(500),
    seo_keywords VARCHAR(500),
    percentage INTEGER DEFAULT 0,
    message TEXT,
    model_used VARCHAR(255),
    approval_status VARCHAR(50) DEFAULT 'pending',
    publish_mode VARCHAR(50) DEFAULT 'draft',
    model_selections JSONB DEFAULT '{}'::jsonb,
    quality_preference VARCHAR(50) DEFAULT 'balanced',
    estimated_cost NUMERIC(10,4) DEFAULT 0.0000,
    actual_cost NUMERIC(10,4) DEFAULT 0.0000,
    cost_breakdown JSONB,
    agent_id VARCHAR(100) DEFAULT 'content-agent',
    started_at TIMESTAMP WITH TIME ZONE,
    published_at TIMESTAMP WITH TIME ZONE,
    human_feedback TEXT,
    approved_by VARCHAR(255),
    approval_timestamp TIMESTAMP WITH TIME ZONE,
    approval_notes TEXT,
    progress JSONB DEFAULT '{}'::jsonb,
    tags JSONB DEFAULT '[]'::jsonb,
    task_metadata JSONB DEFAULT '{}'::jsonb,
    request_type VARCHAR(100),
    -- From 009_add_multi_phase_model_tracking.sql
    models_used_by_phase JSONB DEFAULT '{}'::jsonb,
    model_selection_log JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_content_tasks_task_id ON content_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_content_tasks_status ON content_tasks(status);
CREATE INDEX IF NOT EXISTS idx_content_tasks_stage ON content_tasks(stage);
CREATE INDEX IF NOT EXISTS idx_content_tasks_created_at ON content_tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_content_tasks_topic ON content_tasks(topic);
CREATE INDEX IF NOT EXISTS idx_content_tasks_category ON content_tasks(category);
CREATE INDEX IF NOT EXISTS idx_content_tasks_approval_status ON content_tasks(approval_status);
CREATE INDEX IF NOT EXISTS idx_content_tasks_publish_mode ON content_tasks(publish_mode);
CREATE INDEX IF NOT EXISTS idx_content_tasks_quality_preference ON content_tasks(quality_preference);
CREATE INDEX IF NOT EXISTS idx_content_tasks_writing_style_id ON content_tasks(writing_style_id);
CREATE INDEX IF NOT EXISTS idx_content_tasks_model_used ON content_tasks(model_used);

-- ============================================================
-- Task status history (from 001_create_task_status_history.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS task_status_history (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(255) NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    changed_by VARCHAR(100) DEFAULT 'system',
    reason TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_status_history_task_id ON task_status_history(task_id);

-- ============================================================
-- Quality evaluation tables (from 002_quality_evaluation.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS quality_evaluations (
    id SERIAL PRIMARY KEY,
    content_id VARCHAR(255) NOT NULL,
    task_id VARCHAR(255),
    overall_score DECIMAL(3,1) NOT NULL,
    clarity DECIMAL(3,1) NOT NULL,
    accuracy DECIMAL(3,1) NOT NULL,
    completeness DECIMAL(3,1) NOT NULL,
    relevance DECIMAL(3,1) NOT NULL,
    seo_quality DECIMAL(3,1) NOT NULL,
    readability DECIMAL(3,1) NOT NULL,
    engagement DECIMAL(3,1) NOT NULL,
    passing BOOLEAN NOT NULL DEFAULT FALSE,
    feedback TEXT,
    suggestions JSONB DEFAULT '[]'::jsonb,
    evaluated_by VARCHAR(100) NOT NULL DEFAULT 'QualityEvaluator',
    evaluation_method VARCHAR(50) NOT NULL DEFAULT 'pattern-based',
    evaluation_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    refinement_count INTEGER DEFAULT 0,
    is_final BOOLEAN DEFAULT FALSE,
    content_length INTEGER,
    context_data JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_quality_evaluations_content_id ON quality_evaluations(content_id);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_task_id ON quality_evaluations(task_id);

CREATE TABLE IF NOT EXISTS quality_improvement_logs (
    id SERIAL PRIMARY KEY,
    content_id VARCHAR(255) NOT NULL,
    initial_score DECIMAL(3,1) NOT NULL,
    improved_score DECIMAL(3,1) NOT NULL,
    score_improvement DECIMAL(3,1) NOT NULL,
    best_improved_criterion VARCHAR(50),
    best_improvement_points DECIMAL(3,1),
    refinement_type VARCHAR(100),
    changes_made TEXT,
    refinement_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    passed_after_refinement BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS quality_metrics_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    total_evaluations INTEGER DEFAULT 0,
    passing_count INTEGER DEFAULT 0,
    failing_count INTEGER DEFAULT 0,
    pass_rate DECIMAL(5,2) DEFAULT 0.0,
    average_score DECIMAL(3,1) DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Cost tracking (from 002a_cost_logs_table.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS cost_logs (
    id SERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    user_id UUID,
    phase VARCHAR(50) NOT NULL,
    model VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,
    cost_usd DECIMAL(10, 6),
    quality_score FLOAT,
    duration_ms INT,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cost_logs_task_id ON cost_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_cost_logs_created_at ON cost_logs(created_at);

-- ============================================================
-- Training data tables (from 003_training_data_tables.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS orchestrator_training_data (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) NOT NULL UNIQUE,
    user_request TEXT NOT NULL,
    intent VARCHAR(100),
    business_state JSONB DEFAULT '{}'::jsonb,
    execution_plan TEXT,
    execution_result TEXT,
    quality_score DECIMAL(3,2) NOT NULL DEFAULT 0.5,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    source_agent VARCHAR(100),
    source_model VARCHAR(100),
    execution_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS training_datasets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    filters JSONB DEFAULT '{}'::jsonb,
    example_count INTEGER NOT NULL DEFAULT 0,
    avg_quality DECIMAL(3,2),
    file_path VARCHAR(500),
    file_size_bytes BIGINT,
    file_format VARCHAR(50) DEFAULT 'jsonl',
    used_for_fine_tuning BOOLEAN DEFAULT FALSE,
    fine_tune_job_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100),
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS fine_tuning_jobs (
    id SERIAL PRIMARY KEY,
    job_id VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    target_model VARCHAR(50) NOT NULL,
    model_name VARCHAR(255),
    dataset_id INTEGER,
    dataset_version VARCHAR(100),
    training_config JSONB DEFAULT '{}'::jsonb,
    result_model_id VARCHAR(255),
    result_model_path VARCHAR(500),
    training_examples_count INTEGER,
    estimated_cost DECIMAL(10,2),
    actual_cost DECIMAL(10,2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,
    error_message TEXT,
    error_code VARCHAR(100),
    process_id VARCHAR(100),
    api_request_id VARCHAR(255),
    created_by VARCHAR(100),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_patterns (
    id SERIAL PRIMARY KEY,
    pattern_id VARCHAR(255) NOT NULL UNIQUE,
    pattern_type VARCHAR(100),
    pattern_description TEXT,
    pattern_rule JSONB,
    support_count INTEGER,
    confidence DECIMAL(3,2),
    lift DECIMAL(5,2),
    related_intents TEXT[],
    related_tags TEXT[],
    improves_quality BOOLEAN DEFAULT FALSE,
    improves_success BOOLEAN DEFAULT FALSE,
    avg_quality_improvement DECIMAL(3,2),
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_validated_at TIMESTAMP WITH TIME ZONE,
    validation_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- Writing samples (from 004_writing_samples.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS writing_samples (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT,
    content TEXT NOT NULL,
    is_active BOOLEAN DEFAULT FALSE,
    word_count INTEGER,
    char_count INTEGER,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_writing_samples_user_id ON writing_samples(user_id);

-- ============================================================
-- CMS tables (from 008_create_cms_tables.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS categories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    slug VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    slug VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    content TEXT NOT NULL,
    excerpt TEXT,
    featured_image_url VARCHAR(500),
    cover_image_url VARCHAR(500),
    author_id UUID,
    category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
    tag_ids UUID[] DEFAULT '{}',
    status VARCHAR(50) DEFAULT 'draft',
    seo_title VARCHAR(255),
    seo_description VARCHAR(500),
    seo_keywords TEXT,
    created_by UUID,
    updated_by UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_published_at ON posts(published_at);

-- ============================================================
-- Newsletter (from 010_newsletter_subscribers.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company VARCHAR(255),
    interest_categories VARCHAR(500),
    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45),
    user_agent TEXT,
    verified BOOLEAN DEFAULT FALSE,
    verification_token VARCHAR(255),
    verified_at TIMESTAMP WITH TIME ZONE,
    unsubscribed_at TIMESTAMP WITH TIME ZONE,
    unsubscribe_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    marketing_consent BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS campaign_email_logs (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
    campaign_name VARCHAR(255) NOT NULL,
    campaign_id INTEGER,
    email_subject VARCHAR(500),
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    delivery_status VARCHAR(50),
    delivery_error TEXT,
    opened BOOLEAN DEFAULT FALSE,
    opened_at TIMESTAMP WITH TIME ZONE,
    clicked BOOLEAN DEFAULT FALSE,
    clicked_at TIMESTAMP WITH TIME ZONE,
    bounce_type VARCHAR(50),
    bounce_reason TEXT
);

-- ============================================================
-- Admin logging (from 011_admin_logging_table.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name VARCHAR(255) NOT NULL,
    level VARCHAR(20) NOT NULL,
    message TEXT NOT NULL,
    context JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_logs_agent_name ON logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);

-- ============================================================
-- Users and settings tables (expected by auth system)
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id VARCHAR(255) UNIQUE,
    email VARCHAR(255) UNIQUE,
    username VARCHAR(255),
    display_name VARCHAR(255),
    avatar_url VARCHAR(500),
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) NOT NULL UNIQUE,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    description TEXT,
    updated_by UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Financial tracking (from 012_financial_tracking_table.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS financial_entries (
    id SERIAL PRIMARY KEY,
    entry_type VARCHAR(50) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    description TEXT,
    category VARCHAR(100),
    date DATE NOT NULL DEFAULT CURRENT_DATE,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Agent status tracking (from 013_agent_status_tracking_table.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_status (
    id SERIAL PRIMARY KEY,
    agent_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'idle',
    current_task_id VARCHAR(255),
    last_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""


async def up(pool):
    """Apply the base schema — creates all foundational tables."""
    async with pool.acquire() as conn:
        # Execute each statement separately since asyncpg doesn't support
        # multi-statement execution in a single call
        statements = [s.strip() for s in BASE_SCHEMA_SQL.split(";") if s.strip()]
        for statement in statements:
            # Skip comments-only blocks
            lines = [l for l in statement.split("\n") if l.strip() and not l.strip().startswith("--")]
            if not lines:
                continue
            try:
                await conn.execute(statement + ";")
            except Exception as e:
                # Log but continue — IF NOT EXISTS means most errors are
                # from already-existing objects on production databases
                import logging
                logging.getLogger(__name__).warning(
                    f"Base schema statement skipped (likely already exists): {e}"
                )


async def down(pool):
    """Rollback is intentionally a no-op — dropping base tables is too dangerous."""
