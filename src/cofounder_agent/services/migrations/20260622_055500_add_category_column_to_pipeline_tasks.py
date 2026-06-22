"""Migration: add the missing ``category`` column to ``pipeline_tasks``.

The canonical baseline (``0000_baseline.schema.sql``) defines
``pipeline_tasks.category`` — the claim path
(``services/flows/content_generation.py::claim_pending_task``) SELECTs it, the
flow body consumes it, the row-mirror trigger writes ``NEW.category``, a view
reads ``pt.category``, and ``create_post`` supplies it. But installs whose
``pipeline_tasks`` table predates the baseline's addition of the column never
received it: ``CREATE TABLE IF NOT EXISTS`` cannot add a column to a table that
already exists, and no ALTER migration ever backfilled it. The drift stayed
invisible until a worker on current code tried to claim a task, at which point
``claim_pending_task`` crashed every flow run with
``asyncpg.exceptions.UndefinedColumnError: column "category" does not exist`` —
silently halting the whole content pipeline (no task could be claimed).

This adds the column idempotently so drifted installs match the canonical
schema. ``ADD COLUMN IF NOT EXISTS`` is a no-op where the column already exists
(fresh installs from the baseline, and the live prod DB once hotfixed). The
``integration_db`` ``test_claim_pending_task`` tier — which builds its schema by
running this migration chain — also gains the column, so its three claim tests
go green.

Pure additive DDL: imports only stdlib, so the migrations-smoke CI step applies
it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS category character varying"
        )
    logger.info(
        "add_category_column_to_pipeline_tasks up: "
        "ensured pipeline_tasks.category exists (claim path restored)"
    )


async def down(pool) -> None:
    # No-op: ``category`` is part of the canonical schema (baseline + claim
    # SELECT + mirror trigger + view). Dropping it would re-break the claim
    # path, and the column is additive + nullable, so there is nothing to
    # reverse.
    logger.info(
        "add_category_column_to_pipeline_tasks down: no-op (column is canonical)"
    )
