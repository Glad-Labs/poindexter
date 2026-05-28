"""Retire DETERMINISTIC_COMPOSITOR + the other dead writer_rag_modes.

The dev_diary niche moved to ``services/atoms/narrate_bundle.py`` in
the Phase 3 atom migration — its template skips ``generate_content``
entirely, so ``writer_rag_mode = 'DETERMINISTIC_COMPOSITOR'`` on the
``niches`` row has been a dead sentinel for weeks (it set the value
on every dev_diary task, but no code path read it during execution).

Same cleanup also retires three never-used modes — TOPIC_ONLY,
CITATION_BUDGET, STORY_SPINE — which carried zero pipeline_tasks in
the last 90 days. Their Python files were deleted in this PR; this
migration is the data side: null out ``niches.writer_rag_mode`` for
any niche still pointing at a deleted mode, and drop the orphan
app_settings rows tied to those modes' tunables.

Idempotent: every UPDATE/DELETE filters on the deleted-mode set,
so a re-run is a no-op.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_RETIRED_MODES = (
    "DETERMINISTIC_COMPOSITOR",
    "TOPIC_ONLY",
    "CITATION_BUDGET",
    "STORY_SPINE",
)

_RETIRED_SETTING_KEYS = (
    "writer_rag_citation_budget_snippet_limit",
    "writer_rag_story_spine_snippet_limit",
    "writer_rag_story_spine_snippet_max_chars",
    "writer_rag_topic_only_snippet_limit",
)


async def up(pool) -> None:
    """Null out retired writer_rag_modes + drop their orphan settings."""
    async with pool.acquire() as conn:
        # 1. Drop the existing allowlist CHECK constraint — it references
        #    the retired mode names, so steps 3-4 (null out / re-add CHECK)
        #    would either be rejected or require listing the dead modes
        #    in the new constraint forever.
        await conn.execute(
            "ALTER TABLE niches DROP CONSTRAINT IF EXISTS niches_writer_rag_mode_check"
        )
        # 2. Make the column nullable — dev_diary uses the narrate_bundle
        #    atom and has no writer_rag_mode at all. NOT NULL was modeling
        #    a contract that no longer holds for every niche.
        await conn.execute(
            "ALTER TABLE niches ALTER COLUMN writer_rag_mode DROP NOT NULL"
        )
        # 2b. Drop the column default — it was 'TOPIC_ONLY', which is now
        #     a retired mode. New niche INSERTs that don't explicitly set
        #     a mode should land NULL (signals "uses an atom / no
        #     dispatcher"), not a defunct sentinel.
        await conn.execute(
            "ALTER TABLE niches ALTER COLUMN writer_rag_mode DROP DEFAULT"
        )
        # 3. Null out niches still pointing at retired modes BEFORE adding
        #    the new CHECK — otherwise the CHECK fails on non-conforming
        #    rows.
        result = await conn.execute(
            "UPDATE niches SET writer_rag_mode = NULL WHERE writer_rag_mode = ANY($1)",
            list(_RETIRED_MODES),
        )
        # 4. Now add the narrower allowlist (NULL OR 'TWO_PASS').
        await conn.execute(
            """
            ALTER TABLE niches ADD CONSTRAINT niches_writer_rag_mode_check
              CHECK (writer_rag_mode IS NULL OR writer_rag_mode = 'TWO_PASS')
            """
        )
        # 5. Drop orphan app_settings rows for retired modes' tunables.
        result2 = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1)",
            list(_RETIRED_SETTING_KEYS),
        )
        logger.info(
            "Migration retire_deterministic_compositor: niches=%s app_settings=%s",
            result, result2,
        )


async def down(pool) -> None:
    """No-op revert.

    Restoring the sentinels would point ``niches.writer_rag_mode`` at
    code that no longer exists in the repo — the dispatcher would
    raise ``unknown writer_rag_mode`` for every task in the niche.
    """
    logger.info(
        "Migration retire_deterministic_compositor down: no-op "
        "(retired modes' Python implementations were deleted in the "
        "same PR; restoring values would crash dispatch)"
    )
