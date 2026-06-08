"""Migration: seed the media_pipeline Stage-2 graph_def (epic poindexter#689).

Inserts the ``media_pipeline`` template row into ``pipeline_templates``. The
template is seeded ``active=true`` but **dormant** — nothing dispatches
``TemplateRunner.run("media_pipeline", …)`` yet (the Gate-1 → Stage-2 trigger
lands in a later plan), so this is a behavior no-op in prod, mirroring how
``canonical_blog`` was seeded before its cutover (#355).

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the spec dict — no heavy deps.
from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(MEDIA_PIPELINE_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO pipeline_templates
                (slug, name, description, version, active, graph_def, created_by)
            VALUES ('media_pipeline', 'Media Pipeline', $1, 1, true, $2::jsonb, 'factory')
            ON CONFLICT (slug) DO UPDATE
               SET graph_def   = EXCLUDED.graph_def,
                   description  = EXCLUDED.description,
                   version      = EXCLUDED.version,
                   active       = EXCLUDED.active,
                   updated_at   = NOW()
            """,
            MEDIA_PIPELINE_GRAPH_DEF["description"],
            graph_def_json,
        )
    logger.info(
        "Migration seed_media_pipeline_graph_def up: seeded media_pipeline "
        "Stage-2 template (dormant, #689). result=%s",
        result,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM pipeline_templates WHERE slug = 'media_pipeline'",
        )
    logger.info(
        "Migration seed_media_pipeline_graph_def down: removed media_pipeline "
        "template. result=%s",
        result,
    )
