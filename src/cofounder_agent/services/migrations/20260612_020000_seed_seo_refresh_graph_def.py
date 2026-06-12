"""Migration: seed the seo_refresh graph_def into pipeline_templates (active).

SEO Harvest Loop Phase 2 (#763). The seo_refresh template re-optimizes an
existing post's title/meta (meta_only) gated on operator approval, then
republishes. Seeding the graph_def active means a pipeline_tasks row with
template_slug='seo_refresh' runs this graph via TemplateRunner.

This is INERT by default: no task carries template_slug='seo_refresh' until the
analyzer's enqueuer (Milestone B, gated on app_settings.seo.refresh.enabled,
default false) creates one. So seeding the active template changes no behavior
until an operator opts in.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot. Mirrors 20260611_155929_reseed_canonical_blog_graph_def_v5_seo_collapsed.py.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the pure-data spec dict — no heavy deps so this runs cleanly in
# the migrations-smoke CI environment.
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
        "Migration seed_seo_refresh_graph_def up: seeded seo_refresh template "
        "(%d nodes, active). result=%s",
        len(SEO_REFRESH_GRAPH_DEF["nodes"]),
        result,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pipeline_templates WHERE slug = 'seo_refresh'"
        )
    logger.info("Migration seed_seo_refresh_graph_def down: removed seo_refresh template")
