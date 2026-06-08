"""Migration: re-seed media_pipeline graph_def with the Plan-4 render nodes.

The Plan-2 spine migration (``20260608_120000_seed_media_pipeline_graph_def``)
seeded a single-node ``media_pipeline`` template. Plan 4 (#675) adds two render
nodes (``media.render_long_video`` → ``media.render_short_video``) to
``MEDIA_PIPELINE_GRAPH_DEF``. This re-seed upserts that updated spec into the
existing ``pipeline_templates`` row so prod (where the spine row already exists)
picks up the render nodes in place.

Like the spine seed, the template stays ``active=true`` but **dormant** —
nothing dispatches ``TemplateRunner.run("media_pipeline", …)`` yet (the
Gate-1 → Stage-2 trigger lands in a later plan), so this is a behavior no-op
in prod, mirroring how ``canonical_blog`` was re-seeded between revisions.

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
        "Migration reseed_media_pipeline_render_nodes up: re-seeded "
        "media_pipeline graph_def with Plan-4 render nodes (#675). result=%s",
        result,
    )


async def down(pool) -> None:
    # No-op reversal: this is a re-seed, not a create. The media_pipeline row
    # is intentionally retained — the spine migration owns the row's lifecycle.
    # Reverting the graph_def content is not worthwhile (the spine spec is the
    # only prior state and is still importable), so down() leaves the row as-is.
    logger.info(
        "Migration reseed_media_pipeline_render_nodes down: no-op — render "
        "nodes re-seed; media_pipeline row intentionally retained.",
    )
