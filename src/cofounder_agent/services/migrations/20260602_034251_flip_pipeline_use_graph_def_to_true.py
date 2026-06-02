"""Migration: flip pipeline_use_graph_def to true (#355 atom-cutover Plan 5)

Big-bang cutover: make canonical_blog run as the seeded graph_def (the qa.*
rail atoms replacing cross_model_qa) instead of the legacy Python factory,
which this PR deletes. The Plan-4 migration seeded this key 'false'; flip it
'true' here. Operators can still toggle it (this is the last migration that
sets it). dev_diary has no graph_def row, so it falls back to its (retained)
legacy factory even with the flag true.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "pipeline_use_graph_def", "true",
        )
    logger.info("Migration flip_pipeline_use_graph_def: set true")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'false' WHERE key = 'pipeline_use_graph_def'"
        )
    logger.info("Migration flip_pipeline_use_graph_def down: set false")
