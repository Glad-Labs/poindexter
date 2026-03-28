"""
Migration 0057: Hybrid architecture

Introduces tables and columns for the Glad Labs hybrid architecture:

- Creates `capability_registry` table for tracking workers, agents, and services
- Creates `media_assets` table for unified media/image storage
- Creates `distribution_channels` table for social platform connections
- Creates `social_posts` table for scheduled/published social content
- Creates `routing_outcomes` table for tracking task routing decisions
- Creates `content_calendar` table for editorial planning
- Adds task_category, compute_preference, assigned_worker, worker_claimed_at,
  is_urgent, parent_task_id, and tenant_id columns to content_tasks
"""

UP = """
-- 1. Capability registry
CREATE TABLE IF NOT EXISTS capability_registry (
    id VARCHAR(100) PRIMARY KEY,
    entity_type VARCHAR(30) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    capabilities JSONB DEFAULT '{}'::jsonb,
    config JSONB DEFAULT '{}'::jsonb,
    health JSONB DEFAULT '{}'::jsonb,
    cost_profile JSONB DEFAULT '{}'::jsonb,
    performance JSONB DEFAULT '{}'::jsonb,
    last_heartbeat TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'starting',
    registered_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_capability_registry_type ON capability_registry(entity_type);
CREATE INDEX IF NOT EXISTS idx_capability_registry_status ON capability_registry(status);
CREATE INDEX IF NOT EXISTS idx_capability_registry_heartbeat ON capability_registry(last_heartbeat);

-- 2. Media assets
CREATE TABLE IF NOT EXISTS media_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    site_id UUID REFERENCES sites(id) ON DELETE SET NULL,
    type VARCHAR(30) NOT NULL,
    source VARCHAR(30) NOT NULL,
    storage_provider VARCHAR(30),
    url VARCHAR(1000),
    storage_path VARCHAR(1000),
    thumbnail_url VARCHAR(1000),
    title VARCHAR(500),
    description TEXT,
    alt_text VARCHAR(500),
    metadata JSONB DEFAULT '{}'::jsonb,
    ai_metadata JSONB DEFAULT '{}'::jsonb,
    task_id VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_media_assets_type ON media_assets(type);
CREATE INDEX IF NOT EXISTS idx_media_assets_site ON media_assets(site_id);
CREATE INDEX IF NOT EXISTS idx_media_assets_task ON media_assets(task_id);

-- 3. Distribution channels
CREATE TABLE IF NOT EXISTS distribution_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    site_id UUID REFERENCES sites(id) ON DELETE SET NULL,
    platform VARCHAR(50) NOT NULL,
    account_name VARCHAR(255),
    credentials_ref VARCHAR(255),
    config JSONB DEFAULT '{}'::jsonb,
    posting_enabled BOOLEAN DEFAULT TRUE,
    optimal_times JSONB DEFAULT '[]'::jsonb,
    last_posted_at TIMESTAMPTZ,
    total_posts INTEGER DEFAULT 0,
    avg_engagement JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_distribution_channels_platform ON distribution_channels(platform);

-- 4. Social posts
CREATE TABLE IF NOT EXISTS social_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    site_id UUID REFERENCES sites(id) ON DELETE SET NULL,
    content_task_id VARCHAR(255),
    channel_id UUID REFERENCES distribution_channels(id) ON DELETE SET NULL,
    platform VARCHAR(50) NOT NULL,
    post_type VARCHAR(30),
    content_text TEXT,
    media_asset_ids UUID[],
    hashtags TEXT[],
    scheduled_at TIMESTAMPTZ,
    published_at TIMESTAMPTZ,
    status VARCHAR(30) DEFAULT 'draft',
    platform_post_id VARCHAR(255),
    engagement JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_social_posts_status ON social_posts(status);
CREATE INDEX IF NOT EXISTS idx_social_posts_scheduled ON social_posts(scheduled_at) WHERE status = 'scheduled';

-- 5. Routing outcomes
CREATE TABLE IF NOT EXISTS routing_outcomes (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(255),
    task_type VARCHAR(100),
    task_category VARCHAR(50),
    worker_id VARCHAR(100),
    model_used VARCHAR(200),
    compute_tier VARCHAR(20),
    estimated_cost FLOAT,
    actual_cost FLOAT,
    quality_score FLOAT,
    duration_ms INTEGER,
    success BOOLEAN,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_routing_outcomes_model ON routing_outcomes(model_used, task_type);
CREATE INDEX IF NOT EXISTS idx_routing_outcomes_worker ON routing_outcomes(worker_id);

-- 6. Content calendar
CREATE TABLE IF NOT EXISTS content_calendar (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    site_id UUID REFERENCES sites(id) ON DELETE SET NULL,
    date DATE NOT NULL,
    content_type VARCHAR(50),
    topic VARCHAR(500),
    notes TEXT,
    priority VARCHAR(20) DEFAULT 'normal',
    source VARCHAR(30) DEFAULT 'manual',
    auto_generated BOOLEAN DEFAULT FALSE,
    task_id VARCHAR(255),
    status VARCHAR(30) DEFAULT 'planned',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_content_calendar_date ON content_calendar(date);
CREATE INDEX IF NOT EXISTS idx_content_calendar_status ON content_calendar(status);

-- 7. Add columns to content_tasks
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS task_category VARCHAR(50) DEFAULT 'content';
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS compute_preference VARCHAR(20) DEFAULT 'auto';
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS assigned_worker VARCHAR(100);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS worker_claimed_at TIMESTAMPTZ;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS is_urgent BOOLEAN DEFAULT FALSE;
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS parent_task_id VARCHAR(255);
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS tenant_id UUID;
CREATE INDEX IF NOT EXISTS idx_content_tasks_worker ON content_tasks(assigned_worker);
CREATE INDEX IF NOT EXISTS idx_content_tasks_category ON content_tasks(task_category);
CREATE INDEX IF NOT EXISTS idx_content_tasks_tenant ON content_tasks(tenant_id);
"""

DOWN = """
-- Drop added content_tasks columns (reverse order)
ALTER TABLE content_tasks DROP COLUMN IF EXISTS tenant_id;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS parent_task_id;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS is_urgent;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS worker_claimed_at;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS assigned_worker;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS compute_preference;
ALTER TABLE content_tasks DROP COLUMN IF EXISTS task_category;

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS social_posts;
DROP TABLE IF EXISTS distribution_channels;
DROP TABLE IF EXISTS media_assets;
DROP TABLE IF EXISTS routing_outcomes;
DROP TABLE IF EXISTS content_calendar;
DROP TABLE IF EXISTS capability_registry;
"""


async def up(pool):
    """Apply migration 0057: hybrid architecture."""
    async with pool.acquire() as conn:
        await conn.execute(UP)


async def down(pool):
    """Revert migration 0057: hybrid architecture."""
    async with pool.acquire() as conn:
        await conn.execute(DOWN)
