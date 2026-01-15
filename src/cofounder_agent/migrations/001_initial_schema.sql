-- Migration: Initial Schema
-- Date: January 13, 2026
-- Purpose: Create base tables for tasks and content management

-- Create tasks table (unified task tracking)
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(255) UNIQUE NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    error_message TEXT,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3
);

-- Create content_tasks table (unified task tracking)
CREATE TABLE IF NOT EXISTS content_tasks (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(255) UNIQUE NOT NULL,
    task_type VARCHAR(100) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    title VARCHAR(500),
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    stage VARCHAR(100) DEFAULT 'research',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb,
    result JSONB,
    error_message TEXT
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_content_tasks_task_id ON content_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_content_tasks_status ON content_tasks(status);
CREATE INDEX IF NOT EXISTS idx_content_tasks_stage ON content_tasks(stage);
CREATE INDEX IF NOT EXISTS idx_content_tasks_created_at ON content_tasks(created_at);

-- Log migration completion
-- This file creates the foundation for all other migrations
