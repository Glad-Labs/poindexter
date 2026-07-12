"""Reseed the image_rebuild graph_def with the finalize (atoms.set_task_status) node.

image_rebuild previously ended at content.persist_draft_images -> END with no
node that finalizes the rebuild JOB row, so a successful run stranded it at
status='in_progress' and the stale-sweep re-ran it in a loop. The graph now ends
with a `finalize` node (atoms.set_task_status, target_status='completed').

Existing prod's stored pipeline_templates.graph_def row won't get the new node
from the baseline (baseline runs once), so UPDATE it here to the current spec —
RAW (unstamped); the boot self-heal (ensure_active_graph_defs_stamped) re-stamps
it same boot. Fresh installs get it from the updated baseline seed.

image_rebuild_spec is pure data (no heavy imports), so importing it in a
migration is safe for the dependency-light migrations-smoke env.

See docs/superpowers/specs/2026-07-12-image-rebuild-terminal-status-design.md.
"""

from __future__ import annotations

import json
import logging

from services.image_rebuild_spec import IMAGE_REBUILD_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Point the active image_rebuild row at the current (raw) graph_def."""
    graph_def_json = json.dumps(IMAGE_REBUILD_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb,
                   updated_at = now()
             WHERE slug = 'image_rebuild'
               AND active = true
            """,
            graph_def_json,
        )
    logger.info("Migration reseed_image_rebuild_finalize: applied (%s)", result)


async def down(_pool) -> None:
    """No-op: the prior graph_def is recoverable from git history / baseline.

    Reverting to the pre-finalize graph would re-introduce the in_progress
    stranding bug, so we intentionally do not restore it.
    """
    logger.info("Migration reseed_image_rebuild_finalize: down is a no-op")
