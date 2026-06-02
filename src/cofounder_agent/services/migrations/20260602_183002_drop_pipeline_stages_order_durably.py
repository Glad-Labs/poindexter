"""Migration: durably remove the dead pipeline.stages.order key.

The #355 cutover-residue migration (20260602_133834) deleted
``app_settings.pipeline.stages.order`` (the legacy static stage order that
listed the deleted ``cross_model_qa`` stage; superseded by the graph_def path,
no live reader). But the key **resurrected on boot** — it was still seeded by
``0000_baseline.seeds.sql``, so it reappeared. This PR removes that seed line
(companion edit) AND re-deletes the row here so it stays gone.

No live reader (the graph_def path resolves stage order from
``canonical_blog_spec`` / ``pipeline_templates.graph_def``). Idempotent DELETE;
``down()`` no-op (dead key).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = 'pipeline.stages.order'"
        )
    logger.info("drop_pipeline_stages_order_durably: removed (%s)", result)


async def down(pool) -> None:
    # No-op: dead legacy key (superseded by the graph_def path). The baseline
    # seed line that recreated it has been removed in the same change.
    logger.info("drop_pipeline_stages_order_durably down: no-op (dead key)")
