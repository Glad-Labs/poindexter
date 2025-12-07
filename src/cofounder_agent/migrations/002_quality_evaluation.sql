-- Migration: Add Quality Evaluation Tables
-- Date: December 6, 2025
-- Purpose: Store content quality evaluation scores and history

-- Create quality_evaluations table for storing detailed scores
CREATE TABLE IF NOT EXISTS quality_evaluations (
    id SERIAL PRIMARY KEY,
    content_id VARCHAR(255) NOT NULL,
    task_id VARCHAR(255),  -- Link to content task if applicable
    
    -- 7-Criteria Scores (0-10 scale)
    overall_score DECIMAL(3,1) NOT NULL,  -- Average of 7 criteria
    clarity DECIMAL(3,1) NOT NULL,         -- Clarity score
    accuracy DECIMAL(3,1) NOT NULL,        -- Accuracy score
    completeness DECIMAL(3,1) NOT NULL,    -- Completeness score
    relevance DECIMAL(3,1) NOT NULL,       -- Relevance score
    seo_quality DECIMAL(3,1) NOT NULL,     -- SEO quality score
    readability DECIMAL(3,1) NOT NULL,     -- Readability score
    engagement DECIMAL(3,1) NOT NULL,      -- Engagement score
    
    -- Results
    passing BOOLEAN NOT NULL DEFAULT FALSE,  -- True if overall_score >= 7.0
    feedback TEXT,                           -- Human-readable feedback
    suggestions JSONB DEFAULT '[]'::jsonb,   -- Array of improvement suggestions
    
    -- Metadata
    evaluated_by VARCHAR(100) NOT NULL DEFAULT 'QualityEvaluator',
    evaluation_method VARCHAR(50) NOT NULL DEFAULT 'pattern-based',  -- pattern-based or llm-based
    evaluation_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Refinement tracking
    refinement_count INTEGER DEFAULT 0,     -- How many times content was refined
    is_final BOOLEAN DEFAULT FALSE,         -- True if this is the final evaluation before publishing
    
    -- Content snapshot (for audit trail)
    content_length INTEGER,                 -- Word count of evaluated content
    context_data JSONB DEFAULT '{}'::jsonb  -- Additional context (keywords, topic, etc.)
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_content_id ON quality_evaluations(content_id);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_task_id ON quality_evaluations(task_id);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_passing ON quality_evaluations(passing);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_overall_score ON quality_evaluations(overall_score DESC);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_timestamp ON quality_evaluations(evaluation_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_quality_evaluations_content_task ON quality_evaluations(content_id, task_id);

-- Create table for quality improvement tracking
CREATE TABLE IF NOT EXISTS quality_improvement_logs (
    id SERIAL PRIMARY KEY,
    content_id VARCHAR(255) NOT NULL,
    
    -- Before and after scores
    initial_score DECIMAL(3,1) NOT NULL,
    improved_score DECIMAL(3,1) NOT NULL,
    score_improvement DECIMAL(3,1) NOT NULL,  -- Calculated: improved - initial
    
    -- Which criteria improved most
    best_improved_criterion VARCHAR(50),  -- clarity, accuracy, completeness, etc.
    best_improvement_points DECIMAL(3,1),
    
    -- Refinement details
    refinement_type VARCHAR(100),  -- auto-refinement, manual-edit, etc.
    changes_made TEXT,               -- Description of changes
    refinement_timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Result
    passed_after_refinement BOOLEAN NOT NULL DEFAULT FALSE
);

-- Create indexes for improvement logs
CREATE INDEX IF NOT EXISTS idx_quality_improvement_logs_content_id ON quality_improvement_logs(content_id);
CREATE INDEX IF NOT EXISTS idx_quality_improvement_logs_timestamp ON quality_improvement_logs(refinement_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_quality_improvement_logs_improvement ON quality_improvement_logs(score_improvement DESC);

-- Create table for quality metrics and trending
CREATE TABLE IF NOT EXISTS quality_metrics_daily (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL UNIQUE,
    
    -- Aggregated metrics
    total_evaluations INTEGER DEFAULT 0,
    passing_count INTEGER DEFAULT 0,
    failing_count INTEGER DEFAULT 0,
    pass_rate DECIMAL(5,2) DEFAULT 0.0,  -- Percentage
    average_score DECIMAL(3,1) DEFAULT 0.0,
    
    -- Score distribution
    score_range_0_3 INTEGER DEFAULT 0,
    score_range_3_5 INTEGER DEFAULT 0,
    score_range_5_7 INTEGER DEFAULT 0,
    score_range_7_9 INTEGER DEFAULT 0,
    score_range_9_10 INTEGER DEFAULT 0,
    
    -- Criterion-specific averages
    avg_clarity DECIMAL(3,1) DEFAULT 0.0,
    avg_accuracy DECIMAL(3,1) DEFAULT 0.0,
    avg_completeness DECIMAL(3,1) DEFAULT 0.0,
    avg_relevance DECIMAL(3,1) DEFAULT 0.0,
    avg_seo_quality DECIMAL(3,1) DEFAULT 0.0,
    avg_readability DECIMAL(3,1) DEFAULT 0.0,
    avg_engagement DECIMAL(3,1) DEFAULT 0.0,
    
    -- Refinement metrics
    total_refinements INTEGER DEFAULT 0,
    avg_refinements_per_content DECIMAL(3,1) DEFAULT 0.0,
    total_improvement_points DECIMAL(5,1) DEFAULT 0.0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for daily metrics
CREATE INDEX IF NOT EXISTS idx_quality_metrics_daily_date ON quality_metrics_daily(date DESC);

-- Add comments
COMMENT ON TABLE quality_evaluations IS 'Detailed quality evaluation results using 7-criteria framework';
COMMENT ON TABLE quality_improvement_logs IS 'Track how content quality improves through refinement cycles';
COMMENT ON TABLE quality_metrics_daily IS 'Daily aggregated quality metrics for trending and reporting';
COMMENT ON COLUMN quality_evaluations.overall_score IS 'Average of 7 criteria (0-10), passing threshold is 7.0';
COMMENT ON COLUMN quality_evaluations.passing IS 'True if overall_score >= 7.0, indicates content is ready for publishing';
COMMENT ON COLUMN quality_evaluations.evaluation_method IS 'pattern-based (fast) or llm-based (accurate)';

-- Verify migration
SELECT 'Quality evaluation tables created successfully' AS status;
