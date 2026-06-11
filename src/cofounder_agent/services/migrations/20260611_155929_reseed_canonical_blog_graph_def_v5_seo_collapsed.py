"""Migration: reseed canonical_blog graph_def v5 — collapse 3 serial SEO atoms
into one structured call (seo.generate_all_metadata, poindexter#734).

The three serial atoms seo.generate_title → seo.generate_description →
seo.extract_keywords made three separate LLM calls totalling ~2.4 min/post.
The new seo.generate_all_metadata atom makes one structured call returning
{title, description, keywords} as JSON, saving ~2 min per run.

This migration re-seeds the canonical_blog graph_def from the updated
services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF (which has the three
seo.* nodes + their two internal edges replaced by the single
seo.generate_all_metadata node, with edges qa_aggregate→seo_all_metadata
and seo_all_metadata→generate_media_scripts).

The three individual atom files are retained as standalone importable units —
only the graph_def wiring changes.

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without a full
app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Import only the pure-data spec dict — no heavy deps (LangGraph, template_runner,
# etc.) so this runs cleanly in the migrations-smoke CI environment.
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
        "Migration reseed_canonical_blog_graph_def_v5_seo_collapsed up: "
        "replaced 3 serial seo.* atoms with seo.generate_all_metadata "
        "(saves ~2 min/post, poindexter#734). result=%s",
        result,
    )


async def down(pool) -> None:
    # Restoring the old three-atom chain from scratch is impractical here.
    # Operators who need to revert should apply the previous canonical_blog
    # seed migration (20260610_230000_drop_dead_qa_guardrails_node.py) or
    # restore from a pipeline_templates row backup.
    logger.warning(
        "Migration reseed_canonical_blog_graph_def_v5_seo_collapsed down: "
        "no-op — restoring the three-atom seo.* chain requires re-applying "
        "the previous graph_def migration. Skipping."
    )
