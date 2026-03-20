-- Rollback: Post Tags Junction Table
-- Reverses: 014_add_post_tags_table.sql
-- WARNING: Destroys all post-tag associations

BEGIN;

DROP INDEX IF EXISTS idx_post_tags_post_id;
DROP INDEX IF EXISTS idx_post_tags_tag_id;
DROP TABLE IF EXISTS post_tags;

COMMIT;
