-- =============================================================================
-- Glad Labs — Golden Database Seed
-- =============================================================================
-- Ships with the Quick Start Guide ($29). This is the "Easy Button."
--
-- Creates the app_settings table and populates ALL required keys with sensible
-- defaults for a new user. Safe to re-run (ON CONFLICT DO NOTHING).
--
-- Usage:
--   psql -h localhost -p 5433 -U gladlabs -d gladlabs_brain -f scripts/seed-database.sql
--   -- or --
--   python scripts/seed-database.py --database-url postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain
-- =============================================================================

-- Table creation (idempotent)
CREATE TABLE IF NOT EXISTS app_settings (
    id          SERIAL PRIMARY KEY,
    key         VARCHAR(255) UNIQUE NOT NULL,
    value       TEXT DEFAULT '',
    category    VARCHAR(100) DEFAULT 'general',
    description TEXT DEFAULT '',
    is_secret   BOOLEAN DEFAULT false,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_app_settings_category ON app_settings (category);

-- =============================================================================
-- API Keys (secrets — user must supply their own)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('anthropic_api_key',   '', 'api_keys', 'Anthropic Claude API key (for cloud fallback)',            true),
('gemini_api_key',      '', 'api_keys', 'Google Gemini API key',                                    true),
('google_api_key',      '', 'api_keys', 'Google AI API key',                                        true),
('mercury_api_token',   '', 'api_keys', 'Mercury banking API token (optional — finance features)',   true),
('openai_api_key',      '', 'api_keys', 'OpenAI API key (optional — cloud fallback)',                true),
('pexels_api_key',      '', 'api_keys', 'Pexels image search API key (free at pexels.com/api)',     false),
('resend_api_key',      '', 'api_keys', 'Resend email delivery API key (optional — newsletters)',   true),
('sentry_dsn',          '', 'api_keys', 'Sentry DSN for error tracking (optional)',                  true),
('serper_api_key',      '', 'api_keys', 'Serper search API key (optional — research stage)',         true)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Auth (auto-generated on first run — leave empty for auto-gen)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('api_token',       '', 'auth', 'API token for frontend-to-backend authentication (auto-generated)', true),
('jwt_secret_key',  '', 'auth', 'JWT signing secret (auto-generated on first start)',                true),
('secret_key',      '', 'auth', 'Application secret key (auto-generated on first start)',            true)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Content Rules
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('content_max_refinement_attempts', '3',    'content', 'Max attempts to refine content quality',        false),
('content_min_word_count',          '800',  'content', 'Minimum word count for blog posts',             false),
('content_target_word_count',       '1500', 'content', 'Target word count for blog posts',              false),
('writing_style_reference',         '',     'content', 'Custom writing style prompt (optional)',         false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- CORS
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('allowed_origins',       'http://localhost:3000,http://localhost:8000', 'cors', 'Comma-separated allowed CORS origins',   false),
('rate_limit_per_minute', '100',                                        'cors', 'Max API requests per minute per client',  false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Cost Controls
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('cost_alert_threshold_pct',           '80',       'cost', 'Alert when spend exceeds this % of limit',                false),
('daily_spend_limit',                  '2.0',      'cost', 'Maximum daily AI spend in USD',                           false),
('electricity_rate_kwh',               '0.12',     'cost', 'Local electricity rate $/kWh (check your utility bill)',   false),
('gpu_idle_watts',                     '25',       'cost', 'GPU idle power draw in watts',                            false),
('gpu_inference_watts',                '300',      'cost', 'GPU average inference power draw in watts',               false),
('monthly_spend_limit',               '10.0',      'cost', 'Maximum monthly AI spend in USD',                         false),
('ollama_electricity_cost_per_1k_tokens', '0.000256', 'cost', 'Ollama electricity cost per 1K tokens (USD)',          false),
('system_idle_watts',                  '100',      'cost', 'Total system idle power draw in watts (CPU+RAM+disk+GPU)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Feature Flags
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('enable_mcp_server',       'false', 'features', 'Enable Model Context Protocol server',                false),
('enable_memory_system',    'true',  'features', 'Enable agent memory system',                          false),
('enable_training_capture', 'false', 'features', 'Enable training data capture from pipeline runs',     false),
('redis_enabled',           'false', 'features', 'Enable Redis for caching and pub/sub',                false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- General / System
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('database_pool_max_size',  '20',    'general', 'Max DB pool connections',                                 false),
('database_pool_min_size',  '5',     'general', 'Min DB pool connections',                                 false),
('development_mode',        'true',  'general', 'Enable development mode',                                 false),
('disable_auth_for_dev',    'true',  'general', 'Disable auth in development',                             false),
('enable_sdxl_warmup',      'false', 'general', 'Warm up SDXL models on startup',                          false),
('gpu_name',                '',      'general', 'GPU model name (auto-detected by detect-hardware.py)',    false),
('gpu_vram_gb',             '0',     'general', 'GPU VRAM in GB (auto-detected by detect-hardware.py)',    false),
('host_home',               '',      'general', 'Host home directory for Docker volume mounts',            false),
('log_to_file',             'true',  'general', 'Write logs to file',                                      false),
('model_role_image_decision', 'ollama/phi4:14b', 'general', 'LLM model for image placement decisions',     false),
('ollama_base_url',         'http://host.docker.internal:11434', 'general', 'Ollama API endpoint',         false),
('operator_id',             'operator', 'general', 'Default operator ID',                                  false),
('grafana_url',             'http://localhost:3000', 'general', 'Grafana URL',                              false),
('grafana_user',            'admin', 'general', 'Grafana admin username',                                   false),
('nvidia_exporter_url',     'http://host.docker.internal:9835/metrics', 'general', 'nvidia-smi metrics exporter', false),
('redis_url',               'redis://localhost:6379/0', 'general', 'Redis connection URL',                  true),
('sdxl_server_url',         'http://host.docker.internal:9836', 'general', 'SDXL image generation server', false),
('sentry_enabled',          'false', 'general', 'Enable Sentry error tracking',                             false),
('video_server_url',        'http://host.docker.internal:9837', 'general', 'Video generation server',      false)
ON CONFLICT (key) DO NOTHING;

-- GPU gaming detection (pause pipeline when gaming)
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('gpu_busy_threshold_percent',  '85',  'general', 'GPU utilization % above which gaming is detected',       false),
('gpu_gaming_check_interval',   '30',  'general', 'Seconds between gaming detection checks',                false),
('gpu_gaming_clear_checks',     '3',   'general', 'Consecutive low-util checks to resume pipeline',         false),
('gpu_gaming_confirm_checks',   '3',   'general', 'Consecutive high-util checks to confirm gaming',         false)
ON CONFLICT (key) DO NOTHING;

-- Hardware depreciation (optional — for cost tracking)
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('hardware_cost_total',          '0',  'general', 'Total PC build cost for depreciation calculation',       false),
('hardware_useful_life_months',  '60', 'general', 'Estimated useful life in months (5 years)',              false)
ON CONFLICT (key) DO NOTHING;

-- Cloudflare R2 (optional — for image/podcast hosting)
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('cloudflare_account_id',     '', 'general', 'Cloudflare account ID (for R2 storage)',             false),
('cloudflare_r2_access_key',  '', 'general', 'Cloudflare R2 access key',                          false),
('cloudflare_r2_bucket',      '', 'general', 'Cloudflare R2 bucket name',                         false),
('cloudflare_r2_endpoint',    '', 'general', 'Cloudflare R2 endpoint URL',                        false),
('cloudflare_r2_secret_key',  '', 'general', 'Cloudflare R2 secret key',                          false),
('cloudflare_r2_token',       '', 'general', 'Cloudflare R2 API token',                           false),
('r2_public_url',             '', 'general', 'Public URL for R2 bucket (optional CDN)',            false)
ON CONFLICT (key) DO NOTHING;

-- TTS / pronunciation (optional)
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('tts_acronym_replacements', '', 'general', 'JSON map of acronym → spoken form for TTS',           false),
('tts_pronunciations',       '', 'general', 'JSON map of word → pronunciation for TTS',            false)
ON CONFLICT (key) DO NOTHING;

-- Location (optional — for electricity rate lookups)
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('location_state', '', 'general', 'US state for utility rate lookups (optional)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- GPU Settings
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('ollama_num_ctx', '8192', 'gpu', 'Ollama context window size — limits KV cache VRAM usage', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Site Identity
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('api_base_url',         'http://localhost:8000',   'identity', 'Backend API base URL',                                 false),
('company_name',         'My Company',              'identity', 'Legal company name',                                   false),
('company_founded_date', '',                        'identity', 'Company founding date (optional)',                      false),
('company_founded_year', '',                        'identity', 'Company founding year (optional)',                      false),
('company_founder_name', '',                        'identity', 'Founder name',                                         false),
('company_age_months',   '',                        'identity', 'Company age in months (update periodically)',           false),
('company_products',     '',                        'identity', 'Known real products (for hallucination checks)',        false),
('company_team_size',    '1',                       'identity', 'Team size for content validation',                     false),
('discord_ops_channel_id', '',                      'identity', 'Discord channel for ops notifications (optional)',     false),
('gpu_model',            '',                        'identity', 'GPU model for brain knowledge (auto-detected)',        false),
('newsletter_from_email','',                        'identity', 'Newsletter sender address (requires Resend)',          false),
('privacy_email',        '',                        'identity', 'Privacy/GDPR contact email',                           false),
('site_domain',          'localhost',               'identity', 'Production domain (no protocol)',                      false),
('site_name',            'My Content Site',         'identity', 'Brand/site name used across all services',             false),
('site_url',             'http://localhost:3000',   'identity', 'Full production URL with protocol',                    false),
('support_email',        '',                        'identity', 'Support contact email',                                false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Image Settings
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('enable_featured_image',    'true',    'image', 'Generate/search featured images for posts',                false),
('image_generation_model',   'sdxl_lightning', 'image', 'AI image generation model (sdxl_base, sdxl_lightning, flux_schnell)', false),
('image_negative_prompt',    'blurry, low quality, watermark, text, logo, cartoon, anime', 'image', 'Negative prompt for all SDXL generations', false),
('image_primary_source',     'pexels',  'image', 'Primary image source: pexels or ai_generation',           false),
('image_style_business',     '',        'image', 'SDXL style prompt for Business posts',                    false),
('image_style_default',      '',        'image', 'Default SDXL style for uncategorized posts',              false),
('image_style_engineering',  '',        'image', 'SDXL style prompt for Engineering posts',                 false),
('image_style_insights',     '',        'image', 'SDXL style prompt for Insights posts',                    false),
('image_style_security',     '',        'image', 'SDXL style prompt for Security posts',                    false),
('image_style_startup',      '',        'image', 'SDXL style prompt for Startup posts',                     false),
('image_style_technology',   '',        'image', 'SDXL style prompt for Technology posts',                  false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Integrations (secrets — user supplies their own)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('cloudinary_api_key',       '', 'integration', 'Cloudinary API key for image hosting (optional)',       true),
('cloudinary_api_secret',    '', 'integration', 'Cloudinary API secret for image hosting (optional)',    true),
('cloudinary_cloud_name',    '', 'integration', 'Cloudinary cloud name',                                 false),
('discord_bot_token',        '', 'integration', 'Discord bot token (optional)',                           true),
('discord_voice_bot_token',  '', 'integration', 'Discord voice bot token (optional)',                     true),
('elevenlabs_api_key',       '', 'integration', 'ElevenLabs TTS API key (optional)',                      true),
('gitea_password',           '', 'integration', 'Gitea admin password (if using self-hosted Gitea)',     true),
('gitea_repo',               '', 'integration', 'Gitea repository (owner/name)',                          false),
('gitea_url',                '', 'integration', 'Gitea server URL',                                       false),
('gitea_user',               '', 'integration', 'Gitea username',                                         false),
('grafana_api_key',          '', 'integration', 'Grafana Cloud service account token (optional)',         true),
('grafana_referral_url',     '', 'integration', 'Grafana referral link (optional)',                       false),
('notion_api_key',           '', 'integration', 'Notion API integration key (optional)',                  true),
('patreon_account',          '', 'integration', 'Patreon account (optional — free podcast hosting)',      false),
('telegram_bot_token',       '', 'integration', 'Telegram bot token for notifications (optional)',        true)
ON CONFLICT (key) DO NOTHING;

INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('devto_api_key',        '', 'integrations', 'Dev.to API key for cross-posting (optional)',            false),
('revalidate_secret',    '', 'integrations', 'Next.js ISR revalidation secret',                        true)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Model Roles (which model does what — auto-configured by detect-hardware.py)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('model_role_code_review',  'ollama/qwen3:8b',   'model_roles', 'Model for code snippets and technical accuracy',           false),
('model_role_creative',     'ollama/qwen3:30b',  'model_roles', 'Model for engaging hooks and narrative structure',          false),
('model_role_critic',       'ollama/gemma3:27b',  'model_roles', 'Model for quality scoring and issue detection',            false),
('model_role_factchecker',  'ollama/gemma3:27b',  'model_roles', 'Model for factual accuracy and catching hallucinations',   false),
('model_role_image_prompt', 'ollama/qwen3:8b',   'model_roles', 'Model for generating SDXL prompts',                        false),
('model_role_seo',          'ollama/qwen3:8b',   'model_roles', 'Model for SEO title/description/keyword generation',       false),
('model_role_summarizer',   'ollama/qwen3:8b',   'model_roles', 'Model for summaries and social media copy',                false),
('model_role_writer',       'ollama/qwen3:30b',  'model_roles', 'Model for long-form content generation',                   false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Model Configuration
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('cloud_api_daily_limit',    '50',             'models', 'Max cloud API calls per day (hard cap)',                     false),
('cloud_api_mode',           'emergency_only', 'models', 'Cloud API usage: disabled, emergency_only, fallback, always', false),
('cloud_api_notify_on_use',  'true',           'models', 'Send alert when a cloud API is used',                        false),
('pipeline_critic_model',    'ollama/gemma3:27b',  'models', 'Model for QA/content review',                            false),
('pipeline_fallback_model',  'ollama/gemma3:27b',  'models', 'Fallback model when primary is unavailable',             false),
('pipeline_seo_model',       'ollama/qwen3:8b',    'models', 'Model for SEO title/description generation',             false),
('pipeline_social_model',    'ollama/qwen3:8b',    'models', 'Model for social media post generation',                 false),
('pipeline_writer_model',    'ollama/qwen3:30b',   'models', 'Model for blog content generation (draft phase)',        false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Notifications
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('discord_ops_webhook_url',  '', 'notifications', 'Discord webhook URL for ops notifications (optional)',    false),
('preview_base_url',         '', 'notifications', 'Base URL for post preview links',                         false),
('telegram_alerts_enabled',  'false', 'notifications', 'Enable/disable Telegram alert notifications',        false),
('telegram_alert_types',     'error,critical', 'notifications', 'Comma-separated alert types to send',      false),
('telegram_chat_id',         '', 'notifications', 'Telegram chat ID for alerts',                              false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Pipeline Configuration
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('approval_ttl_days',             '7',     'pipeline', 'Days before unapproved posts are auto-expired',              false),
('auto_publish_threshold',        '0',     'pipeline', 'Quality score threshold for auto-publishing (0=disabled)',   false),
('content_quality_minimum',       '60',    'pipeline', 'Minimum quality score — below this = auto-reject',          false),
('content_weekly_cap',            '0',     'pipeline', 'Maximum new posts per week (0=unlimited)',                   false),
('daily_budget_usd',              '5.00',  'pipeline', 'Daily LLM API spend budget in USD',                         false),
('daily_post_limit',              '5',     'pipeline', 'Maximum posts to generate per day',                          false),
('default_model_tier',            'free',  'pipeline', 'Default model cost tier (free/budget/standard/premium)',     false),
('max_approval_queue',            '10',    'pipeline', 'Max posts awaiting approval before throttling generation',   false),
('max_posts_per_day',             '3',     'pipeline', 'Maximum posts to publish per day',                           false),
('max_task_retries',              '3',     'pipeline', 'Maximum retry attempts for failed tasks',                    false),
('max_tokens_per_request',        '4096',  'pipeline', 'Maximum output tokens per LLM request',                     false),
('max_tokens_per_task',           '16384', 'pipeline', 'Maximum total tokens (input+output) per content task',      false),
('min_curation_score',            '65',    'pipeline', 'Minimum QA score for human review (below = auto-reject)',   false),
('pipeline_factcheck_model',      '',      'pipeline', 'Model for fact-checking (optional)',                         false),
('pipeline_refinement_model',     '',      'pipeline', 'Model for content refinement (optional)',                    false),
('pipeline_research_model',       '',      'pipeline', 'Model for research stage (optional)',                        false),
('publish_spacing_hours',         '4',     'pipeline', 'Minimum hours between published posts',                     false),
('staging_mode',                  'false', 'pipeline', 'When true, posts go to draft with preview token',           false),
('stale_task_timeout_minutes',    '60',    'pipeline', 'Minutes before a running task is considered stale',          false),
('task_sweep_interval_seconds',   '300',   'pipeline', 'Seconds between stale task sweeps',                         false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Podcast (optional)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('podcast_rss_url', '', 'podcast', 'Podcast RSS feed URL (optional)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- QA Workflows (composable QA pipeline chains)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('qa_workflow_blog_content',    'content_validator,multi_model_qa',         'qa_workflows', 'Blog content QA workflow chain',                          false),
('qa_workflow_premium_content', 'content_validator,multi_model_qa,llm_critic', 'qa_workflows', 'Premium QA with LLM critic — all reviewers must pass', false),
('qa_workflow_quick_check',     'content_validator',                         'qa_workflows', 'Fast validation for bulk content',                        false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Quality Thresholds
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('qa_critical_dimension_floor', '50',  'quality', 'Minimum score on any single quality dimension',   false),
('qa_critic_weight',            '0.6', 'quality', 'Weight for LLM critic in final score',            false),
('qa_final_score_threshold',    '70',  'quality', 'Multi-model QA final approval score threshold',   false),
('qa_overall_score_threshold',  '70',  'quality', 'Minimum overall quality score to pass QA (0-100)', false),
('qa_validator_weight',         '0.4', 'quality', 'Weight for programmatic validator in final score', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Security
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('api_auth_token', '', 'security', 'API auth token (alternative to api_token)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Site (legacy keys — kept for backward compatibility with bootstrap.sh)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('public_site_url', 'http://localhost:3000', 'site', 'Public-facing site URL', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Social Media (optional)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('social_linkedin_url', '', 'social', 'LinkedIn profile URL (optional)',  false),
('social_x_handle',     '', 'social', 'X/Twitter handle (optional)',      false),
('social_x_url',        '', 'social', 'X/Twitter profile URL (optional)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- System (internal — usually auto-configured)
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('local_database_url', 'postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain', 'system', 'Local brain DB connection string', false),
('repo_root',          '/app', 'system', 'Root path of the codebase (for running scripts inside container)',                                    false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Token / Generation Settings
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('content_temperature',           '0.7',  'tokens', 'Temperature for content generation',               false),
('max_tokens_default',            '800',  'tokens', 'Default max tokens for general tasks',             false),
('qa_standard_max_tokens',        '300',  'tokens', 'Max tokens for standard models in QA',             false),
('qa_temperature',                '0.3',  'tokens', 'Temperature for QA review generation',             false),
('qa_thinking_model_max_tokens',  '1500', 'tokens', 'Max tokens for thinking models in QA',             false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Webhooks
-- =============================================================================
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('openclaw_webhook_token', '', 'webhooks', 'OpenClaw webhook auth token (optional)', true),
('openclaw_webhook_url',   '', 'webhooks', 'OpenClaw webhook delivery URL (optional)', false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Dev DB compatibility keys (general category — used by older code paths)
-- =============================================================================
-- These exist in the dev DB with 'general' category. The brain DB uses 'identity'
-- and 'integration' categories for the same concepts. These ensure backward compat.
INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
('api_url',              'http://localhost:8000',   'general', 'Backend API base URL (legacy alias for api_base_url)',  false),
('image_model',          'sdxl_lightning',          'general', 'Default image generation model (legacy)',               false),
('newsletter_email',     '',                        'general', 'Newsletter sender email (legacy)',                      false),
('openclaw_gateway_url', 'http://localhost:18789',  'general', 'OpenClaw gateway URL',                                 false),
('owner_email',          '',                        'general', 'Site owner email',                                      false),
('owner_name',           '',                        'general', 'Site owner display name',                               false),
('podcast_description',  '',                        'general', 'Podcast RSS description',                               false),
('podcast_name',         'My Podcast',              'general', 'Podcast title for RSS feeds',                           false),
('site_description',     'AI-powered content platform', 'general', 'Longer site description',                          false),
('site_domain',          'localhost',               'general', 'Bare domain for validation and matching',              false),
('site_name',            'My Content Site',         'general', 'Brand name used in titles, RSS, structured data',      false),
('site_tagline',         'Technology & Innovation', 'general', 'Short tagline used in metadata',                       false),
('site_url',             'http://localhost:3000',   'general', 'Canonical site URL with protocol',                     false),
('company_name',         'My Company',              'general', 'Legal company name for terms/privacy pages',           false),
('privacy_email',        '',                        'general', 'Privacy policy contact email',                          false),
('support_email',        '',                        'general', 'General contact email',                                 false),
('video_feed_name',      'My Video Feed',           'general', 'Video RSS feed title',                                 false)
ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- Done! Summary query
-- =============================================================================
SELECT
    category,
    COUNT(*) as setting_count,
    COUNT(*) FILTER (WHERE is_secret) as secrets,
    COUNT(*) FILTER (WHERE value = '' OR value IS NULL) as needs_config
FROM app_settings
GROUP BY category
ORDER BY category;
