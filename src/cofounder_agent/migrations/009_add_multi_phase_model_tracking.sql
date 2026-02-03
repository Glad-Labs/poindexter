-- Migration: Add multi-phase model tracking
-- Purpose: Track which model was used at each stage of content generation
-- Date: February 2, 2026

-- Add JSON column to store model usage per phase
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS models_used_by_phase JSONB DEFAULT '{}'::jsonb;

-- Add column to track generation flow decisions
ALTER TABLE content_tasks  
ADD COLUMN IF NOT EXISTS model_selection_log JSONB DEFAULT '{}'::jsonb;

-- Create index for faster queries on model_used
CREATE INDEX IF NOT EXISTS idx_content_tasks_model_used ON content_tasks(model_used);

-- Create index for gemini tasks
CREATE INDEX IF NOT EXISTS idx_content_tasks_gemini ON content_tasks (model_used)
WHERE model_used ILIKE '%gemini%';

-- Migration documentation
-- This migration enables tracking of model usage across all phases:
-- {
--   "research": "Google Gemini (gemini-2.5-flash)",
--   "draft": "Google Gemini (gemini-2.5-flash)",
--   "qa": "Ollama (neural-chat:latest)",
--   "image": "Pexels API",
--   "publish": "No model (static)"
-- }
--
-- model_selection_log tracks the decision tree:
-- {
--   "requested_provider": "gemini",
--   "requested_model": "gemini-2.5-flash",
--   "attempted_providers": ["gemini", "ollama"],
--   "skipped_ollama": true,
--   "decision_tree": {
--     "gemini_key_available": true,
--     "gemini_attempted": true,
--     "gemini_succeeded": true,
--     "gemini_error": null
--   }
-- }
