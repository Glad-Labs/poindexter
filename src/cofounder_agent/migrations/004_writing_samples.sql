-- Migration: Add Writing Samples Table for RAG Style Matching
-- Date: January 2026
-- Purpose: Store user writing samples for style matching in LLM prompts

CREATE TABLE IF NOT EXISTS writing_samples (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    
    -- Sample metadata
    title VARCHAR(500) NOT NULL,
    description TEXT,
    
    -- The actual writing sample content
    content TEXT NOT NULL,
    
    -- Track which sample is currently active for this user
    is_active BOOLEAN DEFAULT FALSE,
    
    -- Metadata about the sample
    word_count INTEGER,
    char_count INTEGER,
    
    -- JSON metadata for future enhancements
    -- Could store: detected tone, complexity score, key phrases, vocabulary level, etc.
    metadata JSONB DEFAULT '{}'::jsonb,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Ensure only one sample can be active per user (partial unique constraint)
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_per_user 
    ON writing_samples(user_id, is_active) 
    WHERE is_active = TRUE;

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_writing_samples_user_id 
    ON writing_samples(user_id);

CREATE INDEX IF NOT EXISTS idx_writing_samples_created_at 
    ON writing_samples(created_at DESC);
