-- Migration 026: Add `llm_provider` to app_settings (GH-128).
--
-- Context: 2026-04-25. The content_agent's Config object historically
-- read LLM_PROVIDER via os.getenv("LLM_PROVIDER", "ollama"), violating
-- the DB-first-config policy (CLAUDE.md, services/site_config.py).
-- Anyone flipping the value via app_settings or the OpenClaw settings
-- UI couldn't actually take effect because the env-var read short-
-- circuited it.
--
-- Phase H (GH-95) moved every other tunable to a SiteConfig instance;
-- this migration finishes the job for llm_provider. Default 'ollama'
-- matches the long-standing code default — no behavior change for
-- existing deployments.
--
-- Allowed values: 'ollama' / 'local' (treated identically by
-- LLMClient since v2.8). Paid-API values like 'openai', 'anthropic',
-- and 'gemini' were removed per the no-paid-APIs policy and will
-- raise ValueError at LLMClient init.

INSERT INTO app_settings (key, value, category, description, is_secret) VALUES
    ('llm_provider', 'ollama', 'models', 'LLM provider for the content agent. Allowed: ollama, local. Replaces the legacy LLM_PROVIDER env var.', false)
ON CONFLICT (key) DO NOTHING;
