"""Migration 0142: add DETERMINISTIC_COMPOSITOR writer_rag_mode + apply to dev_diary.

Drops the niches.writer_rag_mode CHECK constraint and re-adds it with the
new DETERMINISTIC_COMPOSITOR value alongside the existing four (TOPIC_ONLY,
CITATION_BUDGET, STORY_SPINE, TWO_PASS). Then sets dev_diary's mode to
DETERMINISTIC_COMPOSITOR so the daily post is rendered template-only,
with no LLM call.

Background: gemma3:27b and qwen3:30b both ignored the strict RAG-summarizer
override prompt and produced fabricated tutorial-style content
("Marek's Dev Diary", invented "daily-diary" tooling, etc.) regardless of
how forcefully the prompt forbade it. Local writer LLMs are pattern-matched
to "produce a blog post" and override system prompts they don't like. The
dev_diary post is structurally a status report whose source of truth is
the context_bundle — there is no creative work for the model to do, just
restating the bundle as Markdown. The deterministic compositor renders it
directly from the bundle (PR title → H2, PR body verbatim → section text,
commits → bullet list, fixed footer). Zero generative step, zero
hallucination surface.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _table_exists(conn, table: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
            "WHERE table_name = $1)",
            table,
        )
    )


async def up(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "niches"):
            logger.info("Migration 0142: niches table missing — skipping")
            return

        # Replace the CHECK constraint on BOTH niches.writer_rag_mode and
        # pipeline_tasks.writer_rag_mode (each table enforces the enum
        # independently). DROP IF EXISTS + ADD keeps this idempotent.
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS niches_writer_rag_mode_check"
        )
        await conn.execute(
            """
            ALTER TABLE niches ADD CONSTRAINT niches_writer_rag_mode_check
              CHECK (writer_rag_mode = ANY (ARRAY[
                  'TOPIC_ONLY'::text,
                  'CITATION_BUDGET'::text,
                  'STORY_SPINE'::text,
                  'TWO_PASS'::text,
                  'DETERMINISTIC_COMPOSITOR'::text
              ]))
            """
        )

        if await _table_exists(conn, "pipeline_tasks"):
            await conn.execute(
                "ALTER TABLE pipeline_tasks DROP CONSTRAINT IF EXISTS pipeline_tasks_writer_rag_mode_check"
            )
            await conn.execute(
                """
                ALTER TABLE pipeline_tasks ADD CONSTRAINT pipeline_tasks_writer_rag_mode_check
                  CHECK (writer_rag_mode IS NULL OR writer_rag_mode = ANY (ARRAY[
                      'TOPIC_ONLY'::text,
                      'CITATION_BUDGET'::text,
                      'STORY_SPINE'::text,
                      'TWO_PASS'::text,
                      'DETERMINISTIC_COMPOSITOR'::text
                  ]))
                """
            )

        result = await conn.execute(
            """
            UPDATE niches
               SET writer_rag_mode = 'DETERMINISTIC_COMPOSITOR', updated_at = NOW()
             WHERE slug = 'dev_diary'
            """
        )
        logger.info(
            "Migration 0142: dev_diary writer_rag_mode set to DETERMINISTIC_COMPOSITOR (%s)",
            result,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        if not await _table_exists(conn, "niches"):
            return
        # Roll dev_diary back to TWO_PASS so the constraint can be tightened.
        await conn.execute(
            "UPDATE niches SET writer_rag_mode = 'TWO_PASS', updated_at = NOW() "
            "WHERE slug = 'dev_diary'"
        )
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS niches_writer_rag_mode_check"
        )
        await conn.execute(
            """
            ALTER TABLE niches ADD CONSTRAINT niches_writer_rag_mode_check
              CHECK (writer_rag_mode = ANY (ARRAY[
                  'TOPIC_ONLY'::text,
                  'CITATION_BUDGET'::text,
                  'STORY_SPINE'::text,
                  'TWO_PASS'::text
              ]))
            """
        )
        logger.info("Migration 0142 down: reverted dev_diary to TWO_PASS + removed mode")
