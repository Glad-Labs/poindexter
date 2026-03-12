"""
Migration 0030: Remove duplicate indexes on content_tasks.

Addresses issue #282: content_tasks has four pairs of duplicate indexes — one set
created by hand-written SQL migrations (idx_ prefix) and a duplicate set created
by SQLAlchemy/Alembic (ix_ prefix). Having both doubles the write overhead for
INSERT/UPDATE on the hottest table and slows autovacuum.

Duplicate pairs:
  status:     idx_content_tasks_status  vs  ix_content_tasks_status
  task_id:    idx_content_tasks_task_id  vs  ix_content_tasks_task_id
  task_type:  idx_content_tasks_task_type  vs  ix_content_tasks_task_type
  created_at: idx_content_tasks_created_at  vs  ix_content_tasks_created_at

The ix_ variants are dropped (SQLAlchemy-generated). The idx_ hand-written
variants are retained as the canonical set.

Note: DROP INDEX CONCURRENTLY cannot run inside a transaction block.
asyncpg auto-begins a transaction for each connection.acquire() block, so we
use conn.execute() outside a transaction by setting autocommit=True via the
connection's isolation level workaround. Actually, since asyncpg does not
support CONCURRENT inside transactions, we issue each DROP without CONCURRENTLY
and rely on the fact that content_tasks write volume is low enough that a brief
AccessShareLock is acceptable at migration time.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)

_DUPLICATE_INDEXES = [
    "ix_content_tasks_status",
    "ix_content_tasks_task_id",
    "ix_content_tasks_task_type",
    "ix_content_tasks_created_at",
]


async def _index_exists(conn, index_name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE indexname = $1",
            index_name,
        )
    )


async def up(pool) -> None:
    """Drop SQLAlchemy-generated duplicate indexes on content_tasks."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            logger.warning("Table 'content_tasks' does not exist — skipping")
            return

        for idx in _DUPLICATE_INDEXES:
            if await _index_exists(conn, idx):
                await conn.execute(f"DROP INDEX IF EXISTS {idx}")
                logger.info(f"Dropped duplicate index: {idx}")
            else:
                logger.debug(f"Index {idx} does not exist — skipping")


async def down(pool) -> None:
    """Recreate the dropped ix_ indexes (mirrors what SQLAlchemy would generate)."""
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name = 'content_tasks')"
        )
        if not table_exists:
            return

        index_defs = {
            "ix_content_tasks_status": "CREATE INDEX IF NOT EXISTS ix_content_tasks_status ON content_tasks(status)",
            "ix_content_tasks_task_id": "CREATE INDEX IF NOT EXISTS ix_content_tasks_task_id ON content_tasks(task_id)",
            "ix_content_tasks_task_type": "CREATE INDEX IF NOT EXISTS ix_content_tasks_task_type ON content_tasks(task_type)",
            "ix_content_tasks_created_at": "CREATE INDEX IF NOT EXISTS ix_content_tasks_created_at ON content_tasks(created_at)",
        }
        for name, ddl in index_defs.items():
            await conn.execute(ddl)
            logger.info(f"Recreated index: {name}")
    logger.info("Restored duplicate indexes (rollback of 0030)")
