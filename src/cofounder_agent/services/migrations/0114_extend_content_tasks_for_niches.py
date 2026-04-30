"""Migration 0114: extend content_tasks for niche-aware pipeline handoff.

Adds three columns the new TopicBatchService.handoff path writes:
- niche_slug         (which niche the task belongs to)
- writer_rag_mode    (the writer mode the writer dispatcher reads)
- topic_batch_id     (provenance pointer to the batch the topic came from)

All nullable for backward compat — existing tasks predate niches and stay valid.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 8)
"""

from services.logger_config import get_logger

logger = get_logger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE content_tasks
              ADD COLUMN IF NOT EXISTS niche_slug TEXT,
              ADD COLUMN IF NOT EXISTS writer_rag_mode TEXT
                CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS') OR writer_rag_mode IS NULL),
              ADD COLUMN IF NOT EXISTS topic_batch_id UUID REFERENCES topic_batches(id)
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_tasks_niche ON content_tasks(niche_slug) WHERE niche_slug IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_content_tasks_batch ON content_tasks(topic_batch_id) WHERE topic_batch_id IS NOT NULL"
        )
        logger.info("Extended content_tasks with niche columns (0114)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_batch")
        await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_niche")
        await conn.execute("""
            ALTER TABLE content_tasks
              DROP COLUMN IF EXISTS topic_batch_id,
              DROP COLUMN IF EXISTS writer_rag_mode,
              DROP COLUMN IF EXISTS niche_slug
        """)
        logger.info("Dropped content_tasks niche columns (0114 down)")
