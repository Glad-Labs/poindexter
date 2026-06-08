"""Migration: re-seed media_pipeline graph_def with the Plan-6 media_qa node.

The Plan-5 re-seed (``20260608_140000_reseed_media_pipeline_caption_node``)
seeded the load_scripts → transcribe_narration → render_long → render_short
chain. Plan 6 (#1193) appends a ``media_qa`` node (``media.qa``) AFTER the
renders in ``MEDIA_PIPELINE_GRAPH_DEF``: it QA-checks the rendered videos —
A/V duration sync, caption presence, and a gated/fail-soft frame
human-detection (policy #675) — replacing the audit-era duration+size-only
check. This re-seed upserts that updated spec into the existing
``pipeline_templates`` row so prod (where the chain row already exists) picks
up the media_qa node in place.

Like the prior seeds, the template stays ``active=true`` but **dormant** —
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
        "Migration reseed_media_pipeline_qa_node up: re-seeded "
        "media_pipeline graph_def with the Plan-6 media_qa node "
        "(#1193). result=%s",
        result,
    )


async def down(pool) -> None:
    # No-op reversal: this is a re-seed, not a create. The media_pipeline row
    # is intentionally retained — the spine migration owns the row's lifecycle.
    # Reverting the graph_def content is not worthwhile (the prior caption-node
    # spec is still importable), so down() leaves the row as-is.
    logger.info(
        "Migration reseed_media_pipeline_qa_node down: no-op — media_qa "
        "node re-seed; media_pipeline row intentionally retained.",
    )
