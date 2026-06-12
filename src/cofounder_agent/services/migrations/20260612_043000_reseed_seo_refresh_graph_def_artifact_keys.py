"""Migration: re-seed the seo_refresh graph_def with gate_artifact_keys.

SEO Harvest Loop Phase 2 follow-up (#763). The original seed migration
(20260612_020000_seed_seo_refresh_graph_def.py) has already run on prod and the
runner won't re-run it, so a spec change to SEO_REFRESH_GRAPH_DEF needs its own
re-seed migration to land in the live pipeline_templates row.

What changed: the refresh_gate node now carries gate_artifact_keys
(title / post_slug / seo_title / seo_description / target_query) so the operator
review artifact surfaces the PROPOSED meta change. Without it the gate fell back
to the default artifact keys (topic/title/excerpt/…), which omit the very
seo_title/seo_description the operator is approving — surfaced during the
first real-post validation run (the gate paused but showed nothing reviewable).

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot. Mirrors the original seed migration.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Pure-data spec dict only — no heavy deps (migrations-smoke light env).
from services.seo_refresh_spec import SEO_REFRESH_GRAPH_DEF  # noqa: E402

_UPSERT_SQL = """
INSERT INTO pipeline_templates
    (slug, name, description, version, active, graph_def, created_by)
VALUES ('seo_refresh', 'seo_refresh', $1, 1, true, $2::jsonb, 'migration')
ON CONFLICT (slug) DO UPDATE
   SET graph_def   = EXCLUDED.graph_def,
       description = EXCLUDED.description,
       active      = true,
       updated_at  = NOW()
"""


async def up(pool) -> None:
    graph_def_json = json.dumps(SEO_REFRESH_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            _UPSERT_SQL, SEO_REFRESH_GRAPH_DEF["description"], graph_def_json
        )
    logger.info(
        "Migration reseed_seo_refresh_graph_def_artifact_keys up: re-seeded "
        "seo_refresh template (%d nodes, gate_artifact_keys added). result=%s",
        len(SEO_REFRESH_GRAPH_DEF["nodes"]),
        result,
    )


async def down(pool) -> None:
    # No-op: this is a forward-only re-seed of an existing row. Rolling the
    # graph_def back to the pre-artifact-keys shape would require pinning the
    # old dict here; the column simply reverts on the next re-seed.
    logger.info(
        "Migration reseed_seo_refresh_graph_def_artifact_keys down: no-op "
        "(forward-only graph_def re-seed)"
    )
