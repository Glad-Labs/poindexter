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

-- Log successful initialization
DO $$ BEGIN RAISE NOTICE 'pgvector + embeddings table initialized successfully'; END $$;
