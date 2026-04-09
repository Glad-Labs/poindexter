"""
Migration 0063: Feedback loop tables for ML self-improvement.

These tables close the gap between "what we generated" and "how it performed."
The system currently tracks everything BEFORE publishing but almost nothing AFTER.
This migration adds the post-publish feedback data the AI needs to learn what works.

Tables added:
  1. post_performance      — traffic, engagement, revenue per post over time
  2. content_revisions     — draft history, what changed between versions
  3. external_metrics      — ingested analytics from Google, social platforms
  4. model_performance     — which models produce best results per task type
  5. revenue_events        — connect posts to sales and affiliate revenue
  6. subscriber_events     — email engagement (opens, clicks, unsubs)
  7. experiments           — A/B tests for titles, images, styles
  8. gpu_task_sessions     — link GPU cost to specific content tasks
"""

MIGRATION_ID = "0063"
DESCRIPTION = "Add feedback loop tables for ML self-improvement"


async def up(conn) -> None:
    await conn.execute("""

    -- =========================================================================
    -- 1. POST PERFORMANCE — how content performs after publishing
    -- Populated by: periodic analytics sync (daemon or cron)
    -- Used by: topic scoring agent, content style agent, feedback loops
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS post_performance (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        post_id UUID NOT NULL,
        slug TEXT NOT NULL,

        -- Snapshot metrics (updated periodically)
        views_1d INTEGER DEFAULT 0,
        views_7d INTEGER DEFAULT 0,
        views_30d INTEGER DEFAULT 0,
        views_total INTEGER DEFAULT 0,

        -- Engagement
        avg_time_on_page_seconds NUMERIC(8,2) DEFAULT NULL,
        scroll_depth_pct NUMERIC(5,2) DEFAULT NULL,
        bounce_rate NUMERIC(5,2) DEFAULT NULL,

        -- Social
        shares_twitter INTEGER DEFAULT 0,
        shares_linkedin INTEGER DEFAULT 0,
        shares_other INTEGER DEFAULT 0,
        comments_count INTEGER DEFAULT 0,

        -- SEO
        google_impressions INTEGER DEFAULT 0,
        google_clicks INTEGER DEFAULT 0,
        google_avg_position NUMERIC(6,2) DEFAULT NULL,
        top_keywords TEXT[] DEFAULT '{}',

        -- Revenue attribution
        affiliate_clicks INTEGER DEFAULT 0,
        affiliate_revenue_usd NUMERIC(10,2) DEFAULT 0,
        direct_revenue_usd NUMERIC(10,2) DEFAULT 0,

        -- Metadata
        measured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        period TEXT DEFAULT 'snapshot',  -- 'daily', 'weekly', 'snapshot'

        CONSTRAINT fk_post_performance_post FOREIGN KEY (post_id)
            REFERENCES posts(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_post_performance_post ON post_performance (post_id);
    CREATE INDEX IF NOT EXISTS idx_post_performance_measured ON post_performance (measured_at DESC);

    -- =========================================================================
    -- 2. CONTENT REVISIONS — draft history for learning what edits improve quality
    -- Populated by: content pipeline on each QA iteration
    -- Used by: iterative refinement agent, quality improvement analysis
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS content_revisions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id TEXT NOT NULL,
        post_id UUID DEFAULT NULL,
        revision_number INTEGER NOT NULL DEFAULT 1,

        content TEXT NOT NULL,
        title TEXT DEFAULT NULL,
        word_count INTEGER DEFAULT 0,
        quality_score NUMERIC(5,2) DEFAULT NULL,

        -- What changed
        change_summary TEXT DEFAULT NULL,   -- LLM-generated description of changes
        change_type TEXT DEFAULT NULL,      -- 'initial_draft', 'qa_refinement', 'human_edit', 'prompt_iteration'
        model_used TEXT DEFAULT NULL,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_content_revisions_task ON content_revisions (task_id);
    CREATE INDEX IF NOT EXISTS idx_content_revisions_post ON content_revisions (post_id) WHERE post_id IS NOT NULL;

    -- =========================================================================
    -- 3. EXTERNAL METRICS — ingested data from 3rd party analytics
    -- Populated by: analytics sync daemon (Google Search Console, GA4, social APIs)
    -- Used by: topic scoring, content performance feedback loops
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS external_metrics (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source TEXT NOT NULL,              -- 'google_search_console', 'google_analytics', 'twitter', 'linkedin', 'cloudflare'
        metric_name TEXT NOT NULL,          -- 'impressions', 'clicks', 'ctr', 'position', 'page_views', 'likes'
        metric_value NUMERIC(14,4) NOT NULL,
        dimensions JSONB DEFAULT '{}',     -- {'page': '/posts/slug', 'query': 'keyword', 'country': 'US'}

        -- Association
        post_id UUID DEFAULT NULL,
        slug TEXT DEFAULT NULL,

        -- Time
        date DATE NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

        CONSTRAINT fk_external_metrics_post FOREIGN KEY (post_id)
            REFERENCES posts(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_external_metrics_source_date ON external_metrics (source, date DESC);
    CREATE INDEX IF NOT EXISTS idx_external_metrics_post ON external_metrics (post_id) WHERE post_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_external_metrics_slug ON external_metrics (slug) WHERE slug IS NOT NULL;

    -- =========================================================================
    -- 4. MODEL PERFORMANCE — track which models produce best results
    -- Populated by: task executor after each generation
    -- Used by: model selection agent, cost optimization
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS model_performance (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_name TEXT NOT NULL,           -- 'qwen3:30b', 'llama3.3:70b', etc.
        task_type TEXT NOT NULL,             -- 'draft', 'qa_review', 'seo', 'image_prompt', 'podcast_script'
        task_id TEXT DEFAULT NULL,

        -- Performance
        quality_score NUMERIC(5,2) DEFAULT NULL,
        generation_time_ms INTEGER DEFAULT NULL,
        tokens_input INTEGER DEFAULT 0,
        tokens_output INTEGER DEFAULT 0,

        -- Cost
        cost_usd NUMERIC(10,6) DEFAULT 0,
        gpu_watts_avg NUMERIC(6,1) DEFAULT NULL,
        electricity_cost_usd NUMERIC(10,6) DEFAULT 0,

        -- Outcome (filled in later)
        human_approved BOOLEAN DEFAULT NULL,
        post_published BOOLEAN DEFAULT NULL,
        post_performance_score NUMERIC(5,2) DEFAULT NULL,  -- from post_performance

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_model_performance_model ON model_performance (model_name);
    CREATE INDEX IF NOT EXISTS idx_model_performance_task_type ON model_performance (task_type);
    CREATE INDEX IF NOT EXISTS idx_model_performance_created ON model_performance (created_at DESC);

    -- =========================================================================
    -- 5. REVENUE EVENTS — connect content to money
    -- Populated by: Lemon Squeezy webhooks, affiliate link tracking
    -- Used by: revenue engine, content ROI analysis
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS revenue_events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_type TEXT NOT NULL,           -- 'sale', 'subscription', 'affiliate_click', 'affiliate_sale', 'ad_impression', 'ad_click'
        source TEXT NOT NULL,               -- 'lemon_squeezy', 'affiliate', 'adsense', 'direct'

        -- Revenue
        amount_usd NUMERIC(10,2) DEFAULT 0,
        currency TEXT DEFAULT 'USD',
        recurring BOOLEAN DEFAULT FALSE,

        -- Attribution
        source_post_id UUID DEFAULT NULL,   -- which post led to this revenue
        source_slug TEXT DEFAULT NULL,
        source_url TEXT DEFAULT NULL,        -- the exact URL that led to conversion
        affiliate_id TEXT DEFAULT NULL,

        -- Customer
        customer_email TEXT DEFAULT NULL,
        customer_id TEXT DEFAULT NULL,       -- Lemon Squeezy customer ID

        -- External
        external_id TEXT DEFAULT NULL,       -- Lemon Squeezy order ID, etc.
        external_data JSONB DEFAULT '{}',

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_revenue_events_type ON revenue_events (event_type);
    CREATE INDEX IF NOT EXISTS idx_revenue_events_post ON revenue_events (source_post_id) WHERE source_post_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_revenue_events_created ON revenue_events (created_at DESC);

    -- =========================================================================
    -- 6. SUBSCRIBER EVENTS — email engagement tracking
    -- Populated by: newsletter service, email provider webhooks
    -- Used by: audience analysis, content targeting
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS subscriber_events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        subscriber_id UUID DEFAULT NULL,
        email TEXT DEFAULT NULL,

        event_type TEXT NOT NULL,           -- 'subscribed', 'unsubscribed', 'email_sent', 'email_opened', 'email_clicked', 'bounced'
        event_data JSONB DEFAULT '{}',     -- {'post_slug': 'x', 'link_clicked': 'url', 'subject': '...'}

        -- Association
        post_id UUID DEFAULT NULL,          -- which post the email was about
        campaign_id TEXT DEFAULT NULL,

        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_subscriber_events_type ON subscriber_events (event_type);
    CREATE INDEX IF NOT EXISTS idx_subscriber_events_subscriber ON subscriber_events (subscriber_id) WHERE subscriber_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_subscriber_events_created ON subscriber_events (created_at DESC);

    -- =========================================================================
    -- 7. EXPERIMENTS — A/B testing for titles, images, styles
    -- Populated by: experiment agent (future)
    -- Used by: content optimization, title selection
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS experiments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        experiment_type TEXT NOT NULL,      -- 'title', 'thumbnail', 'excerpt', 'publish_time', 'content_style'
        status TEXT DEFAULT 'running',      -- 'running', 'completed', 'cancelled'

        -- Variants
        variant_a JSONB NOT NULL,           -- {'title': 'Why X Fails', 'post_id': '...'}
        variant_b JSONB NOT NULL,           -- {'title': 'How to Fix X', 'post_id': '...'}

        -- Results
        metric_name TEXT DEFAULT 'views_7d', -- what we're measuring
        variant_a_value NUMERIC(14,4) DEFAULT NULL,
        variant_b_value NUMERIC(14,4) DEFAULT NULL,
        winner TEXT DEFAULT NULL,            -- 'a', 'b', 'inconclusive'
        confidence NUMERIC(5,4) DEFAULT NULL, -- statistical confidence

        -- Associations
        post_id UUID DEFAULT NULL,
        task_id TEXT DEFAULT NULL,

        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ DEFAULT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_experiments_type ON experiments (experiment_type);
    CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments (status) WHERE status = 'running';

    -- =========================================================================
    -- 8. GPU TASK SESSIONS — link GPU cost to specific content tasks
    -- Populated by: task executor (start/end of GPU-intensive work)
    -- Used by: cost analysis, efficiency optimization
    -- =========================================================================
    CREATE TABLE IF NOT EXISTS gpu_task_sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id TEXT NOT NULL,
        phase TEXT NOT NULL,                -- 'draft_generation', 'image_sdxl', 'qa_review', 'podcast_tts'

        -- Timing
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        ended_at TIMESTAMPTZ DEFAULT NULL,
        duration_seconds NUMERIC(8,2) DEFAULT NULL,

        -- GPU metrics
        gpu_model TEXT DEFAULT NULL,        -- 'RTX 5090'
        avg_utilization_pct NUMERIC(5,2) DEFAULT NULL,
        avg_power_watts NUMERIC(6,1) DEFAULT NULL,
        peak_power_watts NUMERIC(6,1) DEFAULT NULL,
        vram_used_mb INTEGER DEFAULT NULL,

        -- Cost
        kwh_consumed NUMERIC(8,4) DEFAULT NULL,
        electricity_rate_kwh NUMERIC(6,4) DEFAULT 0.12,
        electricity_cost_usd NUMERIC(10,6) DEFAULT NULL,

        -- Model
        model_name TEXT DEFAULT NULL,
        tokens_generated INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_gpu_sessions_task ON gpu_task_sessions (task_id);
    CREATE INDEX IF NOT EXISTS idx_gpu_sessions_phase ON gpu_task_sessions (phase);
    CREATE INDEX IF NOT EXISTS idx_gpu_sessions_started ON gpu_task_sessions (started_at DESC);

    """)


async def down(conn) -> None:
    await conn.execute("""
        DROP TABLE IF EXISTS gpu_task_sessions CASCADE;
        DROP TABLE IF EXISTS experiments CASCADE;
        DROP TABLE IF EXISTS subscriber_events CASCADE;
        DROP TABLE IF EXISTS revenue_events CASCADE;
        DROP TABLE IF EXISTS model_performance CASCADE;
        DROP TABLE IF EXISTS external_metrics CASCADE;
        DROP TABLE IF EXISTS content_revisions CASCADE;
        DROP TABLE IF EXISTS post_performance CASCADE;
    """)
