"""Delete two_pass checkpoint threads keyed on topic instead of task id.

ISSUE: Glad-Labs/poindexter#932

The writer's inner LangGraph used ``two_pass-{niche_id}-{topic[:32]}`` as its
checkpoint thread id. That key is not unique per run — on prod, 176 distinct
32-char topic prefixes are shared by more than one ``pipeline_tasks`` row, the
worst by 201 of them — so many runs wrote into a single ever-growing thread.

Worse, ``retention.checkpoint_prune`` finds checkpoints by building
``prefix || task_id``. A thread named after a *topic* can never be produced
that way, so these rows were unreachable by every retention policy: 7,846 of
them (511 ``checkpoints`` + 5,835 ``checkpoint_writes`` + 1,500
``checkpoint_blobs``) across 30 threads, with nothing able to delete them.

The writer now keys the thread on ``task_id``, which both makes each run's
checkpoints unique and brings them under ``checkpoint_prune`` (``two_pass-``
was added to its default ``thread_prefixes``). This migration removes the
residue left by the old key, which nothing else can reach.

Deliberately scoped to threads that do NOT correspond to a task: any
``two_pass-<task_id>`` thread written by the new code is left alone and will
be pruned normally by its policy.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# checkpoint_writes / checkpoint_blobs reference a thread's checkpoints, so
# they go first — same order retention.checkpoint_prune uses.
_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

# A legacy thread is one prefixed `two_pass-` whose remainder is not a live
# task id. Phrased as NOT EXISTS against pipeline_tasks rather than as a UUID
# regex so it stays correct if the task id format ever changes.
_DELETE = """
    DELETE FROM {table}
     WHERE thread_id LIKE 'two_pass-%'
       AND NOT EXISTS (
             SELECT 1 FROM pipeline_tasks p
              WHERE 'two_pass-' || p.task_id = {table}.thread_id
           )
"""


async def up(pool) -> None:
    """Delete the unreachable legacy-keyed checkpoint rows."""
    async with pool.acquire() as conn:
        for table in _TABLES:
            # The checkpoint tables are created by LangGraph's PostgresSaver,
            # not by our migrations, so a fresh DB that has never run the
            # checkpointer legitimately has none of them.
            exists = await conn.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", table,
            )
            if not exists:
                logger.info(
                    "Migration 20260902_141807: %s absent (checkpointer never "
                    "ran here) — nothing to delete", table,
                )
                continue
            result = await conn.execute(_DELETE.format(table=table))
            logger.info("Migration 20260902_141807: %s -> %s", table, result)
    logger.info("Migration 20260902_141807: applied")


async def down(pool) -> None:
    """One-way: the deleted rows are LangGraph checkpoint state for runs that
    already completed, and there is nothing to restore them from."""
    return
