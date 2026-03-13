"""
Migration 0046: Remove redundant duplicate indexes on agent_status.agent_name
and categories.slug.

Each column already has a UNIQUE constraint whose implementation IS a unique
btree index.  The extra non-unique btree index on the same column doubles
write overhead for no read benefit (the planner always prefers the unique
index for equality lookups).

Indexes removed:
  - idx_agent_status_agent_name  (redundant with agent_status_agent_name_key)
  - idx_categories_slug          (redundant with categories_slug_key)
"""

SQL_UP = """
DROP INDEX IF EXISTS idx_agent_status_agent_name;
DROP INDEX IF EXISTS idx_categories_slug;
"""

SQL_DOWN = """
CREATE INDEX IF NOT EXISTS idx_agent_status_agent_name
    ON agent_status (agent_name);

CREATE INDEX IF NOT EXISTS idx_categories_slug
    ON categories (slug);
"""


async def run_migration(conn) -> None:
    """Apply migration 0046."""
    await conn.execute(SQL_UP)


async def rollback_migration(conn) -> None:
    """Roll back migration 0046."""
    await conn.execute(SQL_DOWN)
