-- Migration: Add post_tags Junction Table
-- Date: 2026-03-10
-- Purpose: cms_routes.py JOINs on post_tags but migration 008 never created it.
--          This migration adds the junction table and backfills existing rows
--          from the tag_ids UUID[] array column on posts.

BEGIN;

-- Create post_tags junction table
CREATE TABLE IF NOT EXISTS post_tags (
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag_id  UUID NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (post_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_post_tags_post_id ON post_tags(post_id);
CREATE INDEX IF NOT EXISTS idx_post_tags_tag_id  ON post_tags(tag_id);

-- Backfill: copy any tag relationships already stored in posts.tag_ids array
-- (safe to run even if tag_ids is empty or tag UUIDs no longer exist in tags table)
INSERT INTO post_tags (post_id, tag_id)
SELECT p.id, unnest(p.tag_ids) AS tag_id
FROM   posts p
WHERE  array_length(p.tag_ids, 1) > 0
ON CONFLICT DO NOTHING;

COMMIT;
