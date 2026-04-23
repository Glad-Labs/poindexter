-- Minimal default settings for Poindexter.
-- This seeds just enough to start the system. For production-quality
-- configuration, see Poindexter Pro at gladlabs.ai/guide.

CREATE TABLE IF NOT EXISTS app_settings (
    id SERIAL PRIMARY KEY,
    key VARCHAR(255) UNIQUE NOT NULL,
    value TEXT DEFAULT '',
    category VARCHAR(100) DEFAULT 'general',
    description TEXT DEFAULT '',
    is_secret BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO app_settings (key, value, category, description) VALUES
-- Minimum required settings
('site_name', 'My Content Site', 'site', 'Your site/brand name'),
('site_url', 'http://localhost:3000', 'site', 'Your production URL'),
('site_domain', 'localhost', 'site', 'Your domain'),
('api_base_url', 'http://localhost:8002', 'site', 'Backend API URL'),
('ollama_base_url', 'http://host.docker.internal:11434', 'infrastructure', 'Ollama API endpoint'),

-- Basic quality settings
('qa_overall_score_threshold', '70', 'quality', 'Minimum score to pass QA'),
('auto_publish_threshold', '0', 'quality', 'Score for auto-publish (0=disabled)'),

-- Basic model settings
('default_ollama_model', 'auto', 'models', 'Default model (auto-selects from available)'),
('pipeline_writer_model', 'ollama/qwen3:8b', 'models', 'Content generation model'),
('pipeline_critic_model', 'ollama/gemma3:27b', 'models', 'QA review model'),
('model_role_image_decision', 'ollama/phi4:14b', 'models', 'Image placement model')

ON CONFLICT (key) DO NOTHING;
