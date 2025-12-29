-- Initialize test database schema for glad_labs_test
-- Run with: psql -h localhost -U postgres -d glad_labs_test -f init_test_schema.sql

-- Memories table: Core persistent memory storage
CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    memory_type VARCHAR(50) NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    tags TEXT[],
    related_memories UUID[],
    metadata JSONB,
    embedding bytea,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge clusters table: Grouped related memories
CREATE TABLE IF NOT EXISTS knowledge_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    memories UUID[],
    confidence REAL NOT NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    importance_score REAL NOT NULL,
    topics TEXT[],
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Learning patterns table: Patterns discovered from interactions
CREATE TABLE IF NOT EXISTS learning_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_type VARCHAR(50) NOT NULL,
    description TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    confidence REAL NOT NULL,
    examples TEXT[],
    discovered_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User preferences table: Persistent user preferences
CREATE TABLE IF NOT EXISTS user_preferences (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source VARCHAR(100)
);

-- Conversation sessions table: Track conversation history
CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    message_count INTEGER DEFAULT 0,
    topics TEXT[],
    summary TEXT,
    importance REAL DEFAULT 0.5
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_clusters_importance ON knowledge_clusters(importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON conversation_sessions(started_at DESC);

-- Verify tables were created
\dt
