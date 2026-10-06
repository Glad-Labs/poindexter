"""Migration 20261006_150927: record the content flow run that claimed each task

Adds ``pipeline_tasks.content_flow_run_id`` — the Prefect flow run that claimed
the row — so the brain's stuck-flow probe can read a RUNNING run's OWN
heartbeat (``last_progress_at``) instead of guessing it from "the in_progress
task".

That guess broke at the end of every run. ``content.persist_task`` moves the
task from ``in_progress`` to ``awaiting_approval`` and the graph still has
three nodes to go (``social.generate_drafts``, ``content.record_pipeline_version``,
``content.evaluate_auto_publish``). With no in_progress row the probe fell back
to its flat 30-minute age rule, so any run still finishing when it turned 30
minutes old was cancelled — 7 of 36 runs in the 30 days to 2026-10-06, two of
them later published with no social drafts, no recorded pipeline version and
no auto-publish evaluation. The 2026-10-04 run was cancelled 40 seconds after
its last node started, while it waited for the GPU.

Nullable and unindexed: one row per claimed task, read by run id for at most a
handful of RUNNING runs per probe cycle, and pre-existing rows simply have no
run to point at.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks ADD COLUMN IF NOT EXISTS content_flow_run_id text"
        )
    logger.info("Migration record_the_content_flow_run_that_claimed_each_pipeline_task: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS content_flow_run_id"
        )
    logger.info("Migration record_the_content_flow_run_that_claimed_each_pipeline_task: reverted")
