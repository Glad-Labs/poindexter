"""
Migration 0066: Split content_tasks into focused pipeline tables.

Phase 1 of issue #211. Creates 4 new tables alongside the existing
content_tasks table (zero risk — no reads/writes changed yet):

  1. pipeline_tasks     — task queue and lifecycle
  2. pipeline_versions  — generated content with version history
  3. pipeline_reviews   — multi-reviewer approval workflow
  4. pipeline_distributions — per-target publish tracking

Also seeds the writing_styles app_setting (configurable writing
styles, same pattern as image_styles).

The old content_tasks table is NOT modified or dropped. Phase 2-4
handle the gradual cutover in application code.
"""

SQL_UP = """
-- =================================================================
-- Table 1: pipeline_tasks (the queue)
-- =================================================================
CREATE TABLE IF NOT EXISTS pipeline_tasks (
    id              SERIAL PRIMARY KEY,
    task_id         VARCHAR UNIQUE NOT NULL,
    task_type       VARCHAR NOT NULL DEFAULT 'blog_post',
    topic           VARCHAR NOT NULL,
    status          VARCHAR NOT NULL DEFAULT 'pending',
    stage           VARCHAR NOT NULL DEFAULT 'pending',
    site_id         UUID,

    -- Task parameters (input)
    style           VARCHAR DEFAULT 'technical',
    tone            VARCHAR DEFAULT 'professional',
    target_length   INTEGER DEFAULT 1500,
    category        VARCHAR,
    primary_keyword VARCHAR,
    target_audience VARCHAR,

    -- Progress tracking
    percentage      INTEGER DEFAULT 0,
    message         TEXT,
    model_used      VARCHAR,
    error_message   TEXT,

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status
    ON pipeline_tasks (status);
CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_status_created
    ON pipeline_tasks (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_stage
    ON pipeline_tasks (stage);

-- =================================================================
-- Table 2: pipeline_versions (generated content + version history)
-- =================================================================
CREATE TABLE IF NOT EXISTS pipeline_versions (
    id                   SERIAL PRIMARY KEY,
    task_id              VARCHAR NOT NULL REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE,
    version              INTEGER NOT NULL DEFAULT 1,

    -- Generated content
    title                VARCHAR,
    content              TEXT,
    excerpt              TEXT,

    -- Image
    featured_image_url   VARCHAR,

    -- SEO
    seo_title            VARCHAR,
    seo_description      VARCHAR,
    seo_keywords         VARCHAR,

    -- Quality
    quality_score        INTEGER,
    qa_feedback          TEXT,

    -- Model tracking
    models_used_by_phase JSONB DEFAULT '{}',

    -- Flexible storage for stage-specific data
    stage_data           JSONB DEFAULT '{}',

    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(task_id, version)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_versions_task
    ON pipeline_versions (task_id);

-- =================================================================
-- Table 3: pipeline_reviews (multi-reviewer approvals)
-- =================================================================
CREATE TABLE IF NOT EXISTS pipeline_reviews (
    id          SERIAL PRIMARY KEY,
    task_id     VARCHAR NOT NULL REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE,
    version     INTEGER NOT NULL DEFAULT 1,
    reviewer    VARCHAR NOT NULL,

    decision    VARCHAR NOT NULL,
    feedback    TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_reviews_task
    ON pipeline_reviews (task_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_reviews_task_decision
    ON pipeline_reviews (task_id, decision);

-- =================================================================
-- Table 4: pipeline_distributions (per-target publish tracking)
-- =================================================================
CREATE TABLE IF NOT EXISTS pipeline_distributions (
    id              SERIAL PRIMARY KEY,
    task_id         VARCHAR NOT NULL REFERENCES pipeline_tasks(task_id) ON DELETE CASCADE,
    target          VARCHAR NOT NULL,

    status          VARCHAR NOT NULL DEFAULT 'pending',
    external_id     VARCHAR,
    external_url    VARCHAR,
    post_id         UUID,
    post_slug       VARCHAR,
    published_at    TIMESTAMPTZ,
    error_message   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(task_id, target)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_distributions_task
    ON pipeline_distributions (task_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_distributions_target_status
    ON pipeline_distributions (target, status);

-- =================================================================
-- Seed writing_styles in app_settings (same pattern as image_styles)
-- =================================================================
INSERT INTO app_settings (key, value, category, description, is_secret)
VALUES (
    'writing_styles',
    '[{"name": "technical", "voice": "precise, detailed, code examples, show-dont-tell", "audience": "developers and engineers", "tone_notes": "confident but not arrogant, practical over theoretical", "enabled": true}, {"name": "founder", "voice": "casual first-person, lessons learned, conversational storytelling", "audience": "indie hackers and solo founders", "tone_notes": "authentic, vulnerable about failures, action-oriented", "enabled": true}, {"name": "explainer", "voice": "ELI5, heavy use of analogies, no jargon, build intuition", "audience": "beginners and non-technical readers", "tone_notes": "patient, encouraging, never condescending", "enabled": true}, {"name": "opinion", "voice": "strong takes, contrarian positions, backed by data", "audience": "senior devs and CTOs", "tone_notes": "provocative but fair, acknowledge counterarguments", "enabled": false}]',
    'content',
    'Configurable writing styles for content generation. Same pattern as image_styles. Each style has name, voice, audience, tone_notes, enabled.',
    false
)
ON CONFLICT (key) DO NOTHING;
"""

SQL_DOWN = """
DROP TABLE IF EXISTS pipeline_distributions CASCADE;
DROP TABLE IF EXISTS pipeline_reviews CASCADE;
DROP TABLE IF EXISTS pipeline_versions CASCADE;
DROP TABLE IF EXISTS pipeline_tasks CASCADE;
DELETE FROM app_settings WHERE key = 'writing_styles';
"""


async def run_migration(conn) -> None:
    """Apply migration 0066 — create pipeline tables."""
    await conn.execute(SQL_UP)


async def rollback_migration(conn) -> None:
    """Rollback migration 0066 — drop pipeline tables."""
    await conn.execute(SQL_DOWN)
