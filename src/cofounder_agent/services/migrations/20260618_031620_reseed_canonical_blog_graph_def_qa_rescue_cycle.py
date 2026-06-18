"""Migration: reseed canonical_blog graph_def — add the QA rescue cycle.

Adds the qa.rewrite node + the branch edge (qa_aggregate -> qa_rewrite) and the
loop edge (qa_rewrite -> qa_programmatic) so a critic-vetoed / below-threshold
draft gets one bounded revision pass before it is hard-rejected. The graph_def
source of truth is services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF (now
37 nodes); this migration writes json.dumps(that) into the active
canonical_blog pipeline_templates row.

The rescue is gated by app_settings.qa_rewrite_max_attempts (default 1, seeded
in settings_defaults.py) and qa.aggregate's is_rescuable_reject predicate — it
never rescues a fabrication / gate / missing_required veto.

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
        "Migration reseed_canonical_blog_graph_def_qa_rescue_cycle up: "
        "added qa.rewrite node + branch/loop edges (37 nodes). result=%s",
        result,
    )


async def down(pool) -> None:
    # Reverting requires re-applying the previous canonical_blog seed migration
    # (20260611_155929_reseed_canonical_blog_graph_def_v5_seo_collapsed.py) or
    # restoring the pipeline_templates row from backup. No-op here.
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_qa_rescue_cycle down: "
        "no-op — re-apply the previous graph_def seed migration to revert."
    )
