-- Migration 023: Add configurable settings for hardcoded values
-- These keys allow multi-site reusability by making previously hardcoded
-- values editable via app_settings.

INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
    ('tts_voice', 'en-US-AndrewMultilingualNeural', 'content', 'Edge TTS voice for podcast generation', false),
    ('stage_timeout_draft', '480', 'pipeline', 'Timeout in seconds for draft generation stage', false),
    ('default_chat_model', 'auto', 'models', 'Default Ollama model for chat', false),
    ('site_url', 'https://www.gladlabs.io', 'identity', 'Public site URL used for canonical links and cross-posting', false)
ON CONFLICT (key) DO NOTHING;
