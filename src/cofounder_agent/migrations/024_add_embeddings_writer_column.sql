-- Migration 024: Add `writer` and `origin_path` columns to embeddings table.
--
-- Context: 2026-04-11 shared-memory architecture planning (Gitea #192).
-- Today `source_table` doubles as a namespace (memory / posts / issues /
-- audit) but there's no way to tell Claude Code memory from OpenClaw memory
-- from worker-generated memory — they all land in `source_table='memory'`
-- and rely on a `source_id` prefix convention. `writer` makes origin
-- explicit and indexable; `origin_path` stores the original filename/URL
-- for traceability.
--
-- This migration is additive and backwards compatible. Existing callers
-- that don't set `writer` will insert NULL; new callers pass it explicitly.
-- The backfill covers every row already in the table based on source_id
-- prefixes and source_table values.
--
-- Rollback is straightforward: DROP INDEX + DROP COLUMN.

ALTER TABLE embeddings
    ADD COLUMN IF NOT EXISTS writer varchar(50),
    ADD COLUMN IF NOT EXISTS origin_path text;

CREATE INDEX IF NOT EXISTS idx_embeddings_writer ON embeddings (writer);

-- Backfill based on what we know about current source_id conventions:
--   - memory/claude-code/*           → writer = 'claude-code'
--   - memory/openclaw/*              → writer = 'openclaw'
--   - memory/shared-context/*        → writer = 'shared-context'
--   - posts/*                        → writer = 'worker'
--   - audit/*                        → writer = 'worker'
--   - issues/*                       → writer = 'gitea'
-- Any row that doesn't match one of the above gets 'unknown' so the column
-- is never NULL after this migration.

UPDATE embeddings SET writer = 'claude-code'
    WHERE source_table = 'memory' AND source_id LIKE 'claude-code/%' AND writer IS NULL;

UPDATE embeddings SET writer = 'openclaw'
    WHERE source_table = 'memory' AND source_id LIKE 'openclaw/%' AND writer IS NULL;

UPDATE embeddings SET writer = 'shared-context'
    WHERE source_table = 'memory' AND source_id LIKE 'shared-context/%' AND writer IS NULL;

UPDATE embeddings SET writer = 'worker'
    WHERE source_table IN ('posts', 'audit') AND writer IS NULL;

UPDATE embeddings SET writer = 'gitea'
    WHERE source_table = 'issues' AND writer IS NULL;

UPDATE embeddings SET writer = 'unknown'
    WHERE writer IS NULL;

-- Backfill origin_path from source_id where it's a filesystem-looking path.
-- For URLs/IDs we leave it NULL and rely on source_id + metadata as the
-- canonical reference.
UPDATE embeddings SET origin_path = source_id
    WHERE origin_path IS NULL
      AND source_table = 'memory'
      AND source_id LIKE '%/%';
