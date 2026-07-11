"""Seed the image_rebuild pipeline template.

``poindexter tasks rebuild-images`` used to run the whole rebuild
synchronously inside a worker HTTP request (``ImageRebuildService``),
blocking the CLI for the full render. It now enqueues a pipeline task with
``template_slug='image_rebuild'`` and this graph_def does the work on the
Prefect worker — same atoms, queued instead of blocking.

Inserts the RAW spec (no ``_contract_fp`` stamps on any node): the boot
self-heal (``pipeline_architect.ensure_active_graph_defs_stamped``) stamps it
against the live atom registry on the next worker boot, exactly like the
media/podcast reseed (``20260623_035500_reseed_media_podcast_graph_defs``).

Idempotent: ON CONFLICT (slug) DO UPDATE, so re-running converges. Fresh
installs get the row from this migration too (the Phase F baseline predates
the template, so it is deliberately NOT in ``0000_baseline.seeds.sql``).
"""
from __future__ import annotations

import json
import logging

from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF

logger = logging.getLogger(__name__)

_INSERT_SQL = """
INSERT INTO pipeline_templates (slug, name, description, version, active, graph_def, created_by)
VALUES ($1, $1, $2, 1, true, $3::jsonb, 'migration')
ON CONFLICT (slug) DO UPDATE
    SET graph_def = EXCLUDED.graph_def,
        description = EXCLUDED.description,
        active = true,
        updated_at = now()
"""


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            _INSERT_SQL,
            "image_rebuild",
            IMAGE_REBUILD_GRAPH_DEF["description"],
            json.dumps(IMAGE_REBUILD_GRAPH_DEF),
        )
        logger.info(
            "seed_image_rebuild_template up: seeded image_rebuild graph_def raw "
            "(boot self-heal stamps contracts) -- %s",
            result,
        )


async def down(pool) -> None:
    # Deactivate rather than delete: pipeline_tasks rows may reference the
    # slug, and an inactive template is invisible to load_active_graph_def.
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE pipeline_templates SET active = false, updated_at = now() "
            "WHERE slug = 'image_rebuild'"
        )
        logger.info("seed_image_rebuild_template down: deactivated -- %s", result)
