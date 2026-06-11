"""Migration: remove the dead qa.guardrails node from canonical_blog graph_def (#730).

guardrails-ai was uninstalled 2026-05-12; the native re-implementation
(``services/guardrails_rails.py``) is advisory and disabled behind
``guardrails_enabled=false``. The qa.guardrails atom was a no-op that still
consumed execution time on every run. This migration re-seeds the
``canonical_blog`` graph_def from the updated
``services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF`` (which has the node
and both edges — ``qa_deepeval→qa_guardrails`` and ``qa_guardrails→qa_ragas``
— removed, and the direct ``qa_deepeval→qa_ragas`` edge added).

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
        "Migration drop_dead_qa_guardrails_node up: re-seeded canonical_blog "
        "graph_def without qa.guardrails node (#730). result=%s",
        result,
    )


async def down(pool) -> None:
    # Re-inserting the old node is impractical without the old spec; a down
    # migration here would be misleading. Operators who need to revert should
    # re-run the previous canonical_blog seed migration
    # (20260603_010000_rewire_programmatic_validator_gate.py) or restore from
    # the pipeline_templates row backup.
    logger.warning(
        "Migration drop_dead_qa_guardrails_node down: no-op — the qa.guardrails "
        "node was a dead no-op (guardrails-ai uninstalled 2026-05-12). "
        "Re-adding it would restore a non-functional node. Skipping."
    )
