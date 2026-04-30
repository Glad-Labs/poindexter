"""Migration 0114: extend content_tasks for niche-aware pipeline handoff.

Adds three columns the new TopicBatchService.handoff path writes:
- niche_slug         (which niche the task belongs to)
- writer_rag_mode    (the writer mode the writer dispatcher reads)
- topic_batch_id     (provenance pointer to the batch the topic came from)

All nullable for backward compat — existing tasks predate niches and stay valid.

## Two shapes for content_tasks

In current production deployments, ``content_tasks`` is a VIEW over the
underlying ``pipeline_tasks`` + ``pipeline_versions`` tables. In fresh-DB
test environments (and post-migration unified state), it's a TABLE. This
migration handles both:

- TABLE shape  → ALTER TABLE adds the columns + indexes directly
- VIEW shape   → ALTER TABLE adds the columns to ``pipeline_tasks`` (the
                 underlying table that the view is built on), then drops
                 + recreates the ``content_tasks`` view to include them
                 in the projected column list

Indexes are always created on the underlying table (``pipeline_tasks`` for
the view case, ``content_tasks`` for the table case) so they're effective
either way.

Spec: docs/superpowers/specs/2026-04-30-rag-pivot-niche-discovery-design.md
Plan: docs/superpowers/plans/2026-04-30-rag-pivot-niche-discovery.md (Task 8)
"""

import re

from services.logger_config import get_logger

logger = get_logger(__name__)


_RELKIND_QUERY = """
    SELECT relkind FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'content_tasks'
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        relkind = await conn.fetchval(_RELKIND_QUERY)
        # asyncpg returns relkind as a bytes object (Postgres "char" type);
        # normalise to a 1-char string for comparison.
        if isinstance(relkind, (bytes, bytearray)):
            relkind = relkind.decode("ascii")
        if relkind == 'r':
            # Plain table — straightforward ALTER.
            await _up_for_table(conn)
        elif relkind == 'v':
            # View over pipeline_tasks — alter the underlying table, then
            # rebuild the view with the new columns appended.
            await _up_for_view(conn)
        elif relkind is None:
            # Neither table nor view exists — error loud.
            raise RuntimeError(
                "0114: content_tasks does not exist (neither as table nor view); "
                "earlier migration is missing or out of order"
            )
        else:
            raise RuntimeError(
                f"0114: content_tasks has unexpected relkind={relkind!r}; expected 'r' (table) or 'v' (view)"
            )


async def _up_for_table(conn) -> None:
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
    logger.info("Extended content_tasks (TABLE shape) with niche columns (0114)")


async def _up_for_view(conn) -> None:
    # 1. Add the columns to pipeline_tasks (the underlying table).
    await conn.execute("""
        ALTER TABLE pipeline_tasks
          ADD COLUMN IF NOT EXISTS niche_slug TEXT,
          ADD COLUMN IF NOT EXISTS writer_rag_mode TEXT
            CHECK (writer_rag_mode IN ('TOPIC_ONLY','CITATION_BUDGET','STORY_SPINE','TWO_PASS') OR writer_rag_mode IS NULL),
          ADD COLUMN IF NOT EXISTS topic_batch_id UUID REFERENCES topic_batches(id)
    """)

    # 2. Helpful indexes on pipeline_tasks (the indexed table when content_tasks is a view).
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_niche ON pipeline_tasks(niche_slug) WHERE niche_slug IS NOT NULL"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_tasks_batch ON pipeline_tasks(topic_batch_id) WHERE topic_batch_id IS NOT NULL"
    )

    # 3. Recreate the content_tasks view with the niche columns spliced in
    #    before the FROM clause. We splice rather than hardcode the full
    #    view body so any operator-specific column additions in the existing
    #    view definition are preserved.
    view_def = await conn.fetchval(
        "SELECT pg_get_viewdef('content_tasks'::regclass, true)"
    )

    # Splice ", pt.niche_slug, pt.writer_rag_mode, pt.topic_batch_id" before
    # the FROM clause. Match the (whitespace + FROM pipeline_tasks pt) so
    # we don't accidentally hit a different FROM in a subquery.
    splice = ",\n    pt.niche_slug,\n    pt.writer_rag_mode,\n    pt.topic_batch_id"
    new_def, n_subs = re.subn(
        r"(\n\s+FROM pipeline_tasks pt\b)",
        splice + r"\1",
        view_def,
        count=1,
    )
    if n_subs != 1:
        raise RuntimeError(
            "0114: failed to splice niche columns into content_tasks view definition — "
            "view shape doesn't match the expected `FROM pipeline_tasks pt` pattern. "
            "View dump:\n" + view_def
        )

    await conn.execute("DROP VIEW content_tasks")
    await conn.execute(f"CREATE VIEW content_tasks AS {new_def}")
    logger.info("Extended pipeline_tasks (underlying VIEW shape) with niche columns (0114)")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        relkind = await conn.fetchval(_RELKIND_QUERY)
        if isinstance(relkind, (bytes, bytearray)):
            relkind = relkind.decode("ascii")
        if relkind == 'r':
            await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_batch")
            await conn.execute("DROP INDEX IF EXISTS ix_content_tasks_niche")
            await conn.execute("""
                ALTER TABLE content_tasks
                  DROP COLUMN IF EXISTS topic_batch_id,
                  DROP COLUMN IF EXISTS writer_rag_mode,
                  DROP COLUMN IF EXISTS niche_slug
            """)
        elif relkind == 'v':
            # Drop indexes on pipeline_tasks. The view rebuild on down()
            # is symmetric: DROP + CREATE without the niche columns.
            await conn.execute("DROP INDEX IF EXISTS ix_pipeline_tasks_batch")
            await conn.execute("DROP INDEX IF EXISTS ix_pipeline_tasks_niche")
            view_def = await conn.fetchval(
                "SELECT pg_get_viewdef('content_tasks'::regclass, true)"
            )
            stripped = re.sub(
                r",\s*pt\.niche_slug,\s*pt\.writer_rag_mode,\s*pt\.topic_batch_id",
                "",
                view_def,
            )
            await conn.execute("DROP VIEW content_tasks")
            await conn.execute(f"CREATE VIEW content_tasks AS {stripped}")
            await conn.execute("""
                ALTER TABLE pipeline_tasks
                  DROP COLUMN IF EXISTS topic_batch_id,
                  DROP COLUMN IF EXISTS writer_rag_mode,
                  DROP COLUMN IF EXISTS niche_slug
            """)
        logger.info("Dropped content_tasks niche columns (0114 down)")
