"""
Migration 0082: Embeddings index cleanup.

Phase 1 of the DB + embeddings plan (docs/architecture/database-and-embeddings-plan-2026-04-24.md).

1. Drop `idx_embeddings_collapse_candidates` — placeholder from a retention
   system that never shipped. 584 kB, 0 scans since creation (verified via
   pg_stat_user_indexes on 2026-04-24). Dead weight on every INSERT.

2. Add `idx_embeddings_created_at` — no age-ordered index exists today.
   Any future retention job (GH-110) or ad-hoc `WHERE created_at < now() - INTERVAL ...`
   query will do a seq scan without it.

Both are idempotent. CONCURRENTLY is skipped because:
  - DROP: `IF EXISTS` is cheap on a 584 kB dead index; no long-running lock.
  - CREATE: 13k rows = <1s build even with exclusive lock; CONCURRENTLY
    can't run inside a transaction anyway, and the migration runner wraps
    each migration in a transaction.
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


SQL_UP = """
DROP INDEX IF EXISTS public.idx_embeddings_collapse_candidates;

CREATE INDEX IF NOT EXISTS idx_embeddings_created_at
    ON public.embeddings (created_at);
"""


SQL_DOWN = """
DROP INDEX IF EXISTS public.idx_embeddings_created_at;

CREATE INDEX IF NOT EXISTS idx_embeddings_collapse_candidates
    ON public.embeddings (source_table, created_at);
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # The ``embeddings`` table is created by ``infrastructure/local-db/init.sql``
        # at container startup, NOT by a migration (predates the migration
        # runner). On fresh CI databases without init.sql (the migration
        # smoke test, GH-229), the table is absent — so this index-cleanup
        # migration has nothing to operate on. Same defensive pattern as
        # migration 0075 (``_table_exists`` skip).
        table_exists = await conn.fetchval(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' "
            "AND tablename='embeddings'"
        )
        if not table_exists:
            logger.info(
                "0082: embeddings table missing (fresh DB without init.sql) — "
                "skipping index cleanup. Will run on next deploy after the "
                "table is created."
            )
            return

        dead_exists = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='idx_embeddings_collapse_candidates'"
        )
        created_at_exists = await conn.fetchval(
            "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='idx_embeddings_created_at'"
        )

        if dead_exists:
            await conn.execute(
                "DROP INDEX IF EXISTS public.idx_embeddings_collapse_candidates"
            )
            logger.info("0082: Dropped dead idx_embeddings_collapse_candidates")
        else:
            logger.info("0082: idx_embeddings_collapse_candidates already absent — skip")

        if not created_at_exists:
            await conn.execute(
                "CREATE INDEX idx_embeddings_created_at ON public.embeddings (created_at)"
            )
            logger.info("0082: Created idx_embeddings_created_at")
        else:
            logger.info("0082: idx_embeddings_created_at already present — skip")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SQL_DOWN)
        logger.info("0082: Reverted — restored collapse_candidates index, dropped created_at")
