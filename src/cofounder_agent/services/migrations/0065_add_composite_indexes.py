"""
Migration 0065: Add composite indexes for hot query paths.

Composite indexes on (status, created_at) and (status, published_at) eliminate
full table scans when the task executor polls for pending tasks or the content
generator deduplicates against published posts.

Surfaced by: codebase performance audit (2026-04-13).
"""

SQL_UP = """
CREATE INDEX IF NOT EXISTS idx_content_tasks_status_created
    ON content_tasks (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_status_published
    ON posts (status, published_at DESC);
"""

SQL_DOWN = """
DROP INDEX IF EXISTS idx_content_tasks_status_created;
DROP INDEX IF EXISTS idx_posts_status_published;
"""


async def run_migration(conn) -> None:
    """Apply migration 0065."""
    await conn.execute(SQL_UP)


async def rollback_migration(conn) -> None:
    """Roll back migration 0065."""
    await conn.execute(SQL_DOWN)
