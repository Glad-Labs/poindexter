-- Local brain database initialization
-- Runs once when the container is first created

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_trgm for fuzzy text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================
-- Embeddings table — semantic search via pgvector
-- =============================================
CREATE TABLE IF NOT EXISTS embeddings (
    id BIGSERIAL PRIMARY KEY,
    source_table VARCHAR(50) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    chunk_index INTEGER DEFAULT 0,
    text_preview VARCHAR(500),
    embedding_model VARCHAR(100) NOT NULL,
    embedding vector(768),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_table, source_id, chunk_index, embedding_model)
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON embeddings
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings(source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(embedding_model);

-- =============================================
-- Audit log table — pipeline event tracking
-- =============================================
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    event_type VARCHAR(50) NOT NULL,
    source VARCHAR(50) NOT NULL,
    task_id VARCHAR(255),
    details JSONB DEFAULT '{}'::jsonb,
    severity VARCHAR(10) DEFAULT 'info'
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_task_id ON audit_log(task_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_severity ON audit_log(severity);

-- =============================================
-- System devices — Tailscale network inventory
-- =============================================
CREATE TABLE IF NOT EXISTS system_devices (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    tailscale_ip VARCHAR(45) NOT NULL,
    device_type VARCHAR(50),
    os VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Log successful initialization
DO $$ BEGIN RAISE NOTICE 'pgvector + embeddings + audit_log + system_devices initialized'; END $$;
