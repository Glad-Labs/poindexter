"""
Migration 0055: Frontier media firm pivot

Introduces multi-site support, webhook event log, and auto-publish/budget
settings for the Glad Labs frontier media firm pivot.

- Creates `sites` table with a default 'Glad Labs' site
- Creates `webhook_events` table for outbound event delivery tracking
- Adds `site_id` FK columns to content_tasks and posts
- Backfills existing rows with the default site
- Adds auto_publish_threshold and daily_budget_usd settings
"""

UP = """
-- 1. Sites table
CREATE TABLE IF NOT EXISTS sites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    domain VARCHAR(255),
    base_url VARCHAR(500),
    default_category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
    config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Default site
INSERT INTO sites (slug, name, domain)
VALUES ('default', 'Glad Labs', 'glad-labs.com')
ON CONFLICT (slug) DO NOTHING;

-- 3. Webhook events table
CREATE TABLE IF NOT EXISTS webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    delivered BOOLEAN DEFAULT FALSE,
    delivery_attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_undelivered
    ON webhook_events(delivered, created_at) WHERE NOT delivered;

-- 4. Add site_id to existing tables
ALTER TABLE content_tasks ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES sites(id) ON DELETE SET NULL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS site_id UUID REFERENCES sites(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_content_tasks_site_id ON content_tasks(site_id);
CREATE INDEX IF NOT EXISTS idx_posts_site_id ON posts(site_id);

-- 5. Backfill site_id with default site
UPDATE content_tasks SET site_id = (SELECT id FROM sites WHERE slug = 'default') WHERE site_id IS NULL;
UPDATE posts SET site_id = (SELECT id FROM sites WHERE slug = 'default') WHERE site_id IS NULL;

-- 6. Auto-publish and budget settings
INSERT INTO settings (key, value, description) VALUES
    ('auto_publish_threshold', '"0"', 'Quality score threshold for auto-publishing (0=disabled)')
ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value, description) VALUES
    ('daily_budget_usd', '"10.0"', 'Daily LLM API spend budget in USD')
ON CONFLICT (key) DO NOTHING;
"""

DOWN = """
-- Remove settings entries
DELETE FROM settings WHERE key IN ('auto_publish_threshold', 'daily_budget_usd');

-- Drop site_id columns from existing tables
ALTER TABLE content_tasks DROP COLUMN IF EXISTS site_id;
ALTER TABLE posts DROP COLUMN IF EXISTS site_id;

-- Drop webhook_events table
DROP TABLE IF EXISTS webhook_events;

-- Drop sites table (CASCADE removes FK references)
DROP TABLE IF EXISTS sites CASCADE;
"""


async def up(pool):
    """Apply migration 0055: frontier media firm pivot."""
    async with pool.acquire() as conn:
        await conn.execute(UP)


async def down(pool):
    """Revert migration 0055: frontier media firm pivot."""
    async with pool.acquire() as conn:
        await conn.execute(DOWN)
