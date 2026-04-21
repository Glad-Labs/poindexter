"""
Migration 0081: Drop redundant idx_pipeline_versions_task index.

Flagged by the Phase 4 schema audit (gitea#271). The unique compound
index `pipeline_versions_task_id_version_key (task_id, version)` already
covers every `task_id`-only lookup because btree indexes serve prefix
queries efficiently. The stand-alone `idx_pipeline_versions_task (task_id)`
is pure duplication.

Reclaim: minor — a few hundred kB of index storage, freed during the
next autovacuum. Main benefit is one fewer index to maintain on every
INSERT/UPDATE to pipeline_versions.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
DROP INDEX IF EXISTS public.idx_pipeline_versions_task;
"""


SQL_DOWN = """
CREATE INDEX IF NOT EXISTS idx_pipeline_versions_task
    ON public.pipeline_versions (task_id);
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='idx_pipeline_versions_task'"
        )
        if not exists:
            logger.info("0081: idx_pipeline_versions_task already absent — skip")
            return
        await conn.execute(SQL_UP)
        logger.info(
            "0081: Dropped redundant idx_pipeline_versions_task "
            "(compound unique key covers task_id-only lookups)"
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0081: Recreated idx_pipeline_versions_task")
