"""Migration: reseed canonical_blog graph_def — add the director self-critique node.

Inserts the review_video_shot_list node (stage.review_video_shot_list) between
generate_video_shot_list and capture_training_data so the director critiques and
revises its own shot list before Gate 1 (video-quality spec §3.1, Piece 1). The
graph_def source of truth is services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF
(now 38 nodes); this migration writes json.dumps(that) into the active
canonical_blog pipeline_templates row.

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
        "Migration reseed_canonical_blog_graph_def_v6_director_review up: "
        "added review_video_shot_list node (38 nodes). result=%s",
        result,
    )


async def down(pool) -> None:
    # Reverting requires re-applying the previous canonical_blog seed migration
    # (20260618_031620_reseed_canonical_blog_graph_def_qa_rescue_cycle.py) or
    # restoring the pipeline_templates row from backup. No-op here.
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_v6_director_review down: "
        "no-op — re-apply the previous graph_def seed migration to revert."
    )
