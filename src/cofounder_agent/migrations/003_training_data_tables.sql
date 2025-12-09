-- Migration: Add Training Data Management Tables
-- Date: December 2025
-- Purpose: Store training data, datasets, fine-tuning jobs, and learning patterns

-- Create orchestrator_training_data table for storing training examples
CREATE TABLE IF NOT EXISTS orchestrator_training_data (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) NOT NULL UNIQUE,
    
    -- Request and execution details
    user_request TEXT NOT NULL,
    intent VARCHAR(100),  -- Classified intent (e.g., 'content_creation', 'analysis')
    
    -- Business context and execution state
    business_state JSONB DEFAULT '{}'::jsonb,  -- State of business at time of execution
    
    -- Execution plan and result
    execution_plan TEXT,
    execution_result TEXT,
    
    -- Quality metrics
    quality_score DECIMAL(3,2) NOT NULL DEFAULT 0.5,  -- 0.0-1.0 scale
    success BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Tagging system for flexible filtering
    tags TEXT[] DEFAULT ARRAY[]::TEXT[],  -- PRODUCTION, DEVELOPMENT, TEST, LOW_QUALITY, MANUAL_APPROVED, EXCLUDE
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Optional: source, agent, model used
    source_agent VARCHAR(100),
    source_model VARCHAR(100),
    execution_time_ms INTEGER  -- How long execution took
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_execution_id 
    ON orchestrator_training_data(execution_id);
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_quality_score 
    ON orchestrator_training_data(quality_score DESC);
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_intent 
    ON orchestrator_training_data(intent);
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_success 
    ON orchestrator_training_data(success);
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_created_at 
    ON orchestrator_training_data(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orchestrator_training_data_tags 
    ON orchestrator_training_data USING GIN(tags);

-- Create training_datasets table for versioned datasets
CREATE TABLE IF NOT EXISTS training_datasets (
    id SERIAL PRIMARY KEY,
    
    -- Dataset metadata
    name VARCHAR(255) NOT NULL,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    
    -- Filters applied to create this dataset
    filters JSONB DEFAULT '{}'::jsonb,  -- {quality_min, quality_max, exclude_tags, intent_filter, date_range, success_only}
    
    -- Dataset statistics
    example_count INTEGER NOT NULL DEFAULT 0,
    avg_quality DECIMAL(3,2),
    quality_distribution JSONB DEFAULT '{}'::jsonb,  -- {low: 0, medium: 0, high: 0}
    
    -- File export information
    file_path VARCHAR(500),  -- Path to exported JSONL file
    file_size_bytes BIGINT,  -- Size of exported file
    file_format VARCHAR(50) DEFAULT 'jsonl',  -- jsonl, json, csv
    
    -- Fine-tuning association
    used_for_fine_tuning BOOLEAN DEFAULT FALSE,
    fine_tune_job_id VARCHAR(255),  -- Link to fine-tuning job
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100),
    
    UNIQUE(name, version)
);

-- Create indexes for datasets
CREATE INDEX IF NOT EXISTS idx_training_datasets_name 
    ON training_datasets(name);
CREATE INDEX IF NOT EXISTS idx_training_datasets_created_at 
    ON training_datasets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_training_datasets_fine_tune_job_id 
    ON training_datasets(fine_tune_job_id);
CREATE INDEX IF NOT EXISTS idx_training_datasets_used_for_fine_tuning 
    ON training_datasets(used_for_fine_tuning);

-- Create fine_tuning_jobs table to track all fine-tuning operations
CREATE TABLE IF NOT EXISTS fine_tuning_jobs (
    id SERIAL PRIMARY KEY,
    
    -- Job identification
    job_id VARCHAR(255) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed, cancelled
    
    -- Configuration
    target_model VARCHAR(50) NOT NULL,  -- ollama, gemini, claude, gpt4
    model_name VARCHAR(255),  -- e.g., 'mistral', 'gpt-4-turbo', 'claude-opus'
    dataset_id INTEGER,
    dataset_version VARCHAR(100),
    
    -- Training configuration
    training_config JSONB DEFAULT '{}'::jsonb,  -- {learning_rate, epochs, batch_size, etc}
    
    -- Results
    result_model_id VARCHAR(255),  -- ID of the fine-tuned model
    result_model_path VARCHAR(500),  -- Local path if applicable (Ollama)
    
    -- Statistics
    training_examples_count INTEGER,
    estimated_cost DECIMAL(10,2),  -- Cost estimate for paid providers
    actual_cost DECIMAL(10,2),  -- Actual cost if available
    
    -- Timing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_seconds INTEGER,  -- calculated if completed
    
    -- Error handling
    error_message TEXT,
    error_code VARCHAR(100),
    
    -- Process tracking
    process_id VARCHAR(100),  -- For local processes (Ollama)
    api_request_id VARCHAR(255),  -- For API-based providers
    
    created_by VARCHAR(100),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for fine-tuning jobs
CREATE INDEX IF NOT EXISTS idx_fine_tuning_jobs_job_id 
    ON fine_tuning_jobs(job_id);
CREATE INDEX IF NOT EXISTS idx_fine_tuning_jobs_status 
    ON fine_tuning_jobs(status);
CREATE INDEX IF NOT EXISTS idx_fine_tuning_jobs_target_model 
    ON fine_tuning_jobs(target_model);
CREATE INDEX IF NOT EXISTS idx_fine_tuning_jobs_created_at 
    ON fine_tuning_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fine_tuning_jobs_dataset_id 
    ON fine_tuning_jobs(dataset_id);

-- Create learning_patterns table to store discovered patterns
CREATE TABLE IF NOT EXISTS learning_patterns (
    id SERIAL PRIMARY KEY,
    
    -- Pattern identification
    pattern_id VARCHAR(255) NOT NULL UNIQUE,
    pattern_type VARCHAR(100),  -- 'intent_correlation', 'success_factor', 'quality_predictor'
    
    -- Pattern details
    pattern_description TEXT,
    pattern_rule JSONB,  -- The actual pattern rule or condition
    
    -- Metrics
    support_count INTEGER,  -- How many examples support this pattern
    confidence DECIMAL(3,2),  -- 0.0-1.0 scale
    lift DECIMAL(5,2),  -- Improvement factor
    
    -- Associated data
    related_intents TEXT[],  -- Intents this pattern applies to
    related_tags TEXT[],  -- Tags that trigger this pattern
    
    -- Impact
    improves_quality BOOLEAN DEFAULT FALSE,
    improves_success BOOLEAN DEFAULT FALSE,
    avg_quality_improvement DECIMAL(3,2),
    
    -- Metadata
    discovered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_validated_at TIMESTAMP WITH TIME ZONE,
    validation_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);

-- Create indexes for learning patterns
CREATE INDEX IF NOT EXISTS idx_learning_patterns_pattern_id 
    ON learning_patterns(pattern_id);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_pattern_type 
    ON learning_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_is_active 
    ON learning_patterns(is_active);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_discovered_at 
    ON learning_patterns(discovered_at DESC);

-- Create historical data enrichment tables if they don't exist

-- Historical tasks table (for legacy data integration)
CREATE TABLE IF NOT EXISTS orchestrator_historical_tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(255) NOT NULL UNIQUE,
    
    title VARCHAR(500),
    description TEXT,
    topic VARCHAR(100),
    
    -- Performance metrics
    completion_rate DECIMAL(3,2),
    quality_score DECIMAL(3,2),
    engagement_score DECIMAL(3,2),
    
    -- Context
    created_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    
    -- Enrichment data
    related_tasks TEXT[],  -- Array of related task IDs
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Published posts table
CREATE TABLE IF NOT EXISTS orchestrator_published_posts (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(255) NOT NULL UNIQUE,
    
    title VARCHAR(500),
    content TEXT,
    topic VARCHAR(100),
    platform VARCHAR(50),  -- blog, twitter, linkedin, etc
    
    -- Performance
    views INTEGER DEFAULT 0,
    engagement INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    
    published_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE
);

-- Social analytics table
CREATE TABLE IF NOT EXISTS social_post_analytics (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(255) NOT NULL,
    
    platform VARCHAR(50),
    views INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    
    engagement_rate DECIMAL(5,2),
    
    tracked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_post_analytics_post_id 
    ON social_post_analytics(post_id);
CREATE INDEX IF NOT EXISTS idx_social_post_analytics_platform 
    ON social_post_analytics(platform);

-- Web analytics table
CREATE TABLE IF NOT EXISTS web_analytics (
    id SERIAL PRIMARY KEY,
    page_id VARCHAR(255),
    
    sessions INTEGER DEFAULT 0,
    users INTEGER DEFAULT 0,
    page_views INTEGER DEFAULT 0,
    bounce_rate DECIMAL(5,2),
    avg_session_duration INTEGER,  -- seconds
    conversion_rate DECIMAL(5,2),
    
    tracked_date DATE,
    tracked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Financial metrics table
CREATE TABLE IF NOT EXISTS financial_metrics (
    id SERIAL PRIMARY KEY,
    
    metric_type VARCHAR(50),  -- revenue, customer_count, growth_rate, etc
    metric_value DECIMAL(15,2),
    currency VARCHAR(3) DEFAULT 'USD',
    
    period_start DATE,
    period_end DATE,
    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add comment to document the migration
COMMENT ON TABLE orchestrator_training_data IS 'Stores training examples from orchestrator executions with filtering and tagging capabilities';
COMMENT ON TABLE training_datasets IS 'Versioned training datasets with filtered examples exported for fine-tuning';
COMMENT ON TABLE fine_tuning_jobs IS 'Tracks all fine-tuning operations across different model providers';
COMMENT ON TABLE learning_patterns IS 'Discovered patterns and insights from successful executions';
