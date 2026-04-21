"""
Migration 0079: Drop the content_tasks_deprecated orphan table.

Context:
    The content_tasks monolith was split into pipeline_tasks +
    pipeline_versions + the content_tasks view back in issue #211. The
    original table was renamed content_tasks_deprecated as a safety net.
    After 208 posts published + several sessions of the new schema,
    no code references it (grep shows zero hits across services/, routes/,
    tests/, infrastructure/) and no writes have occurred since the rename.

Evidence at migration time:
    - content_tasks_deprecated: 217 rows, 9.5MB allocated
    - Zero code references

Reclaim: ~9.5MB. Low-risk since DROP ... IF EXISTS is idempotent and the
reverse (down) is intentionally a no-op — if someone actually needed the
historical rows they should have pulled a snapshot before running this.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
DROP TABLE IF EXISTS public.content_tasks_deprecated CASCADE;
"""


SQL_DOWN = """
-- Irreversible. Pull from backup if the historical rows are needed.
SELECT 1;
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='content_tasks_deprecated'"
        )
        if not exists:
            logger.info("content_tasks_deprecated not present — skipping 0079")
            return
        row_count = await conn.fetchval(
            "SELECT COUNT(*) FROM content_tasks_deprecated"
        )
        await conn.execute(SQL_UP)
        logger.info(
            "Dropped content_tasks_deprecated (had %s rows). Reclaim ~9.5MB.",
            row_count,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info(
            "0079 down is a no-op — restore from backup if historical rows are needed"
        )
