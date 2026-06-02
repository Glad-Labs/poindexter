"""Migration: seed canonical_blog graph_def + pipeline_use_graph_def flag

ISSUE: Glad-Labs/poindexter#355 (atom-cutover Plan 4)

Seeds the static graph_def spec for canonical_blog into pipeline_templates
(active=true, version=2) — the 13 coarse stages as stage.* nodes + the qa.*
rail atoms replacing the monolithic cross_model_qa stage. Also seeds
app_settings.pipeline_use_graph_def='false' — the master cutover flag.

DORMANT on prod: with the flag false, TemplateRunner ignores the seeded
graph_def and keeps running the legacy Python TEMPLATES factory. Plan 5
flips the flag (per-niche canary, then globally) and then deletes the
legacy factory.

Idempotent: ON CONFLICT (slug) DO UPDATE for the template row, ON CONFLICT
(key) DO NOTHING for the flag (keeps an operator-tuned value).
"""

from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pipeline_templates
              (slug, name, description, version, active, graph_def, created_by)
            VALUES ('canonical_blog', 'Canonical Blog',
                    'Atom-composed canonical_blog pipeline (#355)',
                    2, true, $1::jsonb, 'migration')
            ON CONFLICT (slug) DO UPDATE
              SET graph_def   = EXCLUDED.graph_def,
                  name        = EXCLUDED.name,
                  description = EXCLUDED.description,
                  version     = EXCLUDED.version,
                  active      = EXCLUDED.active,
                  updated_at  = NOW()
            """,
            payload,
        )
        # Master cutover flag — default false so the seeded graph_def stays
        # dormant until Plan 5 flips it. ON CONFLICT keeps an operator value.
        await conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO NOTHING",
            "pipeline_use_graph_def", "false",
        )
        logger.info("Migration seed_canonical_blog_graph_def: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        # Drop the seeded template row + the flag (only if still false).
        await conn.execute(
            "DELETE FROM pipeline_templates WHERE slug = 'canonical_blog' AND created_by = 'migration'"
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = 'pipeline_use_graph_def' AND value = 'false'"
        )
        logger.info("Migration seed_canonical_blog_graph_def down: reverted")
