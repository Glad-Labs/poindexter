-- Migration: Add all required columns for task creation
-- Date: 2026-01-14
-- Purpose: Add missing columns (topic, style, tone, target_length, etc.) needed by tasks_db.py

BEGIN;

-- Add topic column (required for content generation)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS topic VARCHAR(500);

-- Add style column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS style VARCHAR(100) DEFAULT 'technical';

-- Add tone column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS tone VARCHAR(100) DEFAULT 'professional';

-- Add target_length column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS target_length INTEGER DEFAULT 1500;

-- Add primary_keyword column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS primary_keyword VARCHAR(255);

-- Add target_audience column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS target_audience VARCHAR(255);

-- Add category column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS category VARCHAR(100);

-- Add writing_style_id column (FK to writing_samples)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS writing_style_id INTEGER;

-- Add content column (text of generated content)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS content TEXT;

-- Add excerpt column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS excerpt TEXT;

-- Add featured_image_url column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS featured_image_url VARCHAR(500);

-- Add featured_image_data column (JSON with image metadata)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS featured_image_data JSONB;

-- Add featured_image_prompt column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS featured_image_prompt TEXT;

-- Add qa_feedback column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS qa_feedback TEXT;

-- Add quality_score column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS quality_score INTEGER;

-- Add seo_title column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS seo_title VARCHAR(255);

-- Add seo_description column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS seo_description VARCHAR(500);

-- Add seo_keywords column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS seo_keywords VARCHAR(500);

-- Add percentage column (progress tracking)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS percentage INTEGER DEFAULT 0;

-- Add message column (status message)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS message TEXT;

-- Add model_used column (which model was used)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS model_used VARCHAR(255);

-- Add approval_status column (pending, approved, rejected)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS approval_status VARCHAR(50) DEFAULT 'pending';

-- Add publish_mode column (draft, published)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS publish_mode VARCHAR(50) DEFAULT 'draft';

-- Add model_selections column (JSON with model choices per phase)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS model_selections JSONB DEFAULT '{}'::jsonb;

-- Add quality_preference column (fast, balanced, quality)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS quality_preference VARCHAR(50) DEFAULT 'balanced';

-- Add estimated_cost column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(10,4) DEFAULT 0.0000;

-- Add actual_cost column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(10,4) DEFAULT 0.0000;

-- Add cost_breakdown column (JSON with costs by phase)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS cost_breakdown JSONB;

-- Add agent_id column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS agent_id VARCHAR(100) DEFAULT 'content-agent';

-- Add started_at column (when processing began)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS started_at TIMESTAMP WITH TIME ZONE;

-- Add published_at column (when content was published)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS published_at TIMESTAMP WITH TIME ZONE;

-- Add human_feedback column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS human_feedback TEXT;

-- Add approved_by column (user who approved)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS approved_by VARCHAR(255);

-- Add approval_timestamp column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS approval_timestamp TIMESTAMP WITH TIME ZONE;

-- Add approval_notes column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS approval_notes TEXT;

-- Add progress column (JSON with detailed progress tracking)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS progress JSONB DEFAULT '{}'::jsonb;

-- Add tags column (JSON array of tags)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb;

-- Add task_metadata column (JSON metadata)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS task_metadata JSONB DEFAULT '{}'::jsonb;

-- Add error_message column
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS error_message TEXT;

-- Add actual_cost column (cost of processing the task)
ALTER TABLE content_tasks
ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(10, 4) DEFAULT 0.0;

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_content_tasks_topic ON content_tasks(topic);
CREATE INDEX IF NOT EXISTS idx_content_tasks_category ON content_tasks(category);
CREATE INDEX IF NOT EXISTS idx_content_tasks_approval_status ON content_tasks(approval_status);
CREATE INDEX IF NOT EXISTS idx_content_tasks_publish_mode ON content_tasks(publish_mode);
CREATE INDEX IF NOT EXISTS idx_content_tasks_quality_preference ON content_tasks(quality_preference);

COMMIT;
