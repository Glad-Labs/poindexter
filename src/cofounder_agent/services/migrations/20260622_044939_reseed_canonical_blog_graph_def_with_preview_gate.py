"""Migration: reseed canonical_blog graph_def — add the preview_gate node.

Inserts the ``preview_gate`` node (``atoms.approval_gate``) into the finalize
block: ``record_pipeline_version -> preview_gate -> evaluate_auto_publish``, plus
two branch+loop back-edges (``preview_gate -> plan_image_markers`` for
regen_images, ``preview_gate -> generate_draft`` for regen_text). The graph_def
source of truth is services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF (now 39
nodes); this migration writes json.dumps(that) into the active canonical_blog
pipeline_templates row.

The node is INERT until the operator enables it: the atom passes through unless
``app_settings.pipeline_gate_preview_gate`` is on (seeded ``false``). So this
reseed is safe to ship ahead of the default flip — every post gains one
passthrough hop and nothing else changes. See
docs/architecture/2026-06-21-component-scoped-regen-gate.md.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the pure-data spec dict — no heavy deps so this runs cleanly in
# the migrations-smoke CI environment.
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF  # noqa: E402


async def up(pool) -> None:
    graph_def_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def  = $1::jsonb,
                   updated_at = NOW()
             WHERE slug   = 'canonical_blog'
               AND active = true
            """,
            graph_def_json,
        )
    logger.info(
        "Migration reseed_canonical_blog_graph_def_with_preview_gate up: "
        "added preview_gate node (39 nodes, seeded disabled). result=%s",
        result,
    )


async def down(pool) -> None:
    # Reverting requires re-applying the previous canonical_blog seed migration
    # (20260619_182232_reseed_canonical_blog_graph_def_v6_director_review.py) or
    # restoring the pipeline_templates row from backup. No-op here.
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_with_preview_gate down: "
        "no-op — re-apply the previous graph_def seed migration to revert."
    )
