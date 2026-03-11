-- Rollback: CMS Tables
-- Reverses: 008_create_cms_tables.sql
-- WARNING: Destroys all CMS content (posts, categories, tags)

BEGIN;

DROP INDEX IF EXISTS idx_posts_slug;
DROP INDEX IF EXISTS idx_posts_status;
DROP INDEX IF EXISTS idx_posts_created_at;
DROP INDEX IF EXISTS idx_posts_author_id;
DROP INDEX IF EXISTS idx_posts_category_id;
DROP INDEX IF EXISTS idx_tags_slug;
DROP INDEX IF EXISTS idx_categories_slug;

DROP TABLE IF EXISTS post_tags;
DROP TABLE IF EXISTS posts;
DROP TABLE IF EXISTS tags;
DROP TABLE IF EXISTS categories;

COMMIT;
