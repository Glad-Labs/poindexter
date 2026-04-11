-- Migration: Add pg_trgm GIN indexes for ILIKE search on content_tasks
-- Fixes issue #307 — ILIKE '%term%' with leading wildcard causes full table scan.
-- pg_trgm GIN indexes allow PostgreSQL to use the index for ILIKE '%term%' queries.
-- Version: 021
-- NOTE: pg_trgm extension must be available (it is included in PostgreSQL 9.1+ and
--       enabled on most managed Postgres hosts by default, but may need superuser for new installs).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Index for task_name / title searches
CREATE INDEX IF NOT EXISTS idx_content_tasks_title_trgm
  ON content_tasks USING gin (title gin_trgm_ops);

-- Index for topic searches
CREATE INDEX IF NOT EXISTS idx_content_tasks_topic_trgm
  ON content_tasks USING gin (topic gin_trgm_ops);

-- Index for category searches
CREATE INDEX IF NOT EXISTS idx_content_tasks_category_trgm
  ON content_tasks USING gin (category gin_trgm_ops);

COMMIT;
