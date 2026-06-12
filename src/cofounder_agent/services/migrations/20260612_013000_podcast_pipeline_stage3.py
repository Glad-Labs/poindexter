"""Stage-3 podcast pipeline (#689 deviation) — marker + graph_def seed.

Two additive, idempotent changes:

1. ``pipeline_tasks.podcast_dispatched_at`` — the per-medium claim marker the
   ``dispatch_podcast_pipeline`` job stamps before running ``podcast_pipeline``,
   so podcast dispatch is fully independent of the video lane's existing
   ``media_pipeline_dispatched_at`` marker (reject+recreate a video without
   touching the podcast, and vice versa).
2. Seed the ``podcast_pipeline`` Stage-3 ``graph_def`` template row.

Seeded ``active=true`` but the lane is DORMANT until an operator flips
``podcast_pipeline_trigger_enabled`` (default off, in settings_defaults).

(The matching ``video_dispatched_at`` marker + the ``video_long``→``video``
asset-type consolidation belong to the video-side half of #689 — deferred to a
dedicated change, see ``docs/architecture/podcast-pipeline-stage3.md`` §11. The
video lane already has an independent dispatch marker
(``media_pipeline_dispatched_at``), so the podcast split is complete without it.)

Imports only stdlib + the pure-data spec dict (no LangGraph / template_runner)
so the migrations-smoke CI step can apply it without a full app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pipeline_tasks "
            "ADD COLUMN IF NOT EXISTS podcast_dispatched_at timestamp with time zone"
        )
        await conn.execute(
            """
            INSERT INTO pipeline_templates
                (slug, name, description, version, active, graph_def, created_by)
            VALUES ('podcast_pipeline', 'Podcast Pipeline', $1, 1, true, $2::jsonb, 'factory')
            ON CONFLICT (slug) DO UPDATE
               SET graph_def   = EXCLUDED.graph_def,
                   description  = EXCLUDED.description,
                   version      = EXCLUDED.version,
                   active       = EXCLUDED.active,
                   updated_at   = NOW()
            """,
            PODCAST_PIPELINE_GRAPH_DEF["description"],
            json.dumps(PODCAST_PIPELINE_GRAPH_DEF),
        )
    logger.info(
        "Migration podcast_pipeline_stage3 up: ensured podcast_dispatched_at "
        "column and seeded the podcast_pipeline graph_def (#689)."
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pipeline_templates WHERE slug = 'podcast_pipeline'"
        )
        await conn.execute(
            "ALTER TABLE pipeline_tasks DROP COLUMN IF EXISTS podcast_dispatched_at"
        )
    logger.info(
        "Migration podcast_pipeline_stage3 down: removed podcast_pipeline template "
        "and the podcast_dispatched_at column."
    )
