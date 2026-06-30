"""Migration 20260630_011500: add ``pipeline_tasks.trace_context`` (Tier 1b).

Adds a nullable ``jsonb`` column carrying the W3C trace-context carrier
(``{"traceparent": ..., "tracestate": ...}``) of whatever enqueued the task.
``tasks_db.add_task`` stamps it at creation, ``claim_pending_task`` SELECTs it,
and the content-generation flow attaches it around its root span — so a run
links to the trace of the API request / job that created it instead of starting
a disconnected root trace (Glad-Labs/glad-labs-stack#1997 Tier 1b).

Nullable with no default: NULL means "no upstream trace" and the flow starts a
fresh root span exactly as before, so the column is purely additive — rows
created before this migration (and tasks enqueued with no active span) simply
read back NULL and behave as they did pre-Tier-1b.

Not seeded into ``0000_baseline.schema.sql``: per the migrations convention new
schema DDL lives in a timestamped migration; the next baseline squash folds it
in. Fresh installs run the baseline (column absent) then this migration (adds
it); prod runs only this migration.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS trace_context jsonb"
        )
    logger.info(
        "add_pipeline_tasks_trace_context up: added nullable trace_context "
        "jsonb column (no-op where already present)"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS trace_context"
        )
    logger.info(
        "add_pipeline_tasks_trace_context down: dropped the trace_context column"
    )
