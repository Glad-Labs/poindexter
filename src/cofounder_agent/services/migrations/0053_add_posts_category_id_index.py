"""
Migration 0053: Add index on posts.category_id

Addresses issue #999: posts.category_id has no index, causing
sequential scans on JOIN and filtered queries.
"""

UP = """
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_posts_category_id
ON posts (category_id);
"""

DOWN = """
DROP INDEX IF EXISTS idx_posts_category_id;
"""
