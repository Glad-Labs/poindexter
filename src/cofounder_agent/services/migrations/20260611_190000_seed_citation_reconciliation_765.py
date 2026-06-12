"""Migration: citation reconciliation — reseed canonical_blog graph_def v6 +
seed the advisory unlinked_attribution qa_gates row (Glad-Labs/poindexter#765).

Two new nodes wire into the canonical_blog pipeline:

- ``content.reconcile_citations`` (after the writer block, before
  quality_evaluation): deterministic citation repair — at each attribution site
  whose named subject matches a research-corpus source by domain handle, wrap
  the subject in a markdown link to that source's URL. The inserted links then
  flow through qa.citations' dead-link check.
- ``qa.unlinked_attribution`` (after qa.citations): advisory rail scoring the
  residual — named-source attributions left unlinked and unmatched against the
  corpus (author names / unknown brands). Advisory-first, never vetoes.

This reseeds the canonical_blog graph_def from the updated
services/canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF and seeds the
``unlinked_attribution`` qa_gates row (required_to_pass=false, advisory —
matching every new gate added in the 2026-06 batch).

The four app_settings keys (citation_reconcile_enabled,
unlinked_attribution_enabled, unlinked_attribution_penalty_per,
unlinked_attribution_score_floor) live in settings_defaults.py and are seeded
idempotently every boot — NOT here (per CLAUDE.md: settings seeder, not
migrations).

IMPORTANT: imports only stdlib + the pure-data spec dict (no LangGraph /
template_runner) so the migrations-smoke CI step can apply it without an app boot.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Pure-data spec dict only — no heavy deps (LangGraph, template_runner).
from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF  # noqa: E402

_GATE = {
    "name": "unlinked_attribution",
    "stage_name": "qa",
    "execution_order": 160,   # After citation_verifier (150), before consistency (400).
    "reviewer": "unlinked_attribution",
    "required_to_pass": False,   # Advisory-first — scores, never vetoes.
    "enabled": True,
    "metadata": json.dumps({
        "description": "Named-source-cited-without-link advisory rail (#765)",
        "rail": "_citation_match",
        "atom": "qa.unlinked_attribution",
    }),
}


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
        await conn.execute(
            """
            INSERT INTO qa_gates
              (name, stage_name, execution_order, reviewer,
               required_to_pass, enabled, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _GATE["name"], _GATE["stage_name"], _GATE["execution_order"],
            _GATE["reviewer"], _GATE["required_to_pass"], _GATE["enabled"],
            _GATE["metadata"],
        )
    logger.info(
        "Migration seed_citation_reconciliation_765 up: reseeded canonical_blog "
        "graph_def (content.reconcile_citations + qa.unlinked_attribution) and "
        "seeded the unlinked_attribution qa_gates row. result=%s",
        result,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM qa_gates WHERE name = $1", _GATE["name"],
        )
    # Restoring the prior graph_def from scratch is impractical here; operators
    # who need to revert should re-apply the previous canonical_blog seed
    # migration (20260611_155929_reseed_canonical_blog_graph_def_v5_seo_collapsed)
    # or restore the pipeline_templates row from a backup.
    logger.warning(
        "Migration seed_citation_reconciliation_765 down: removed the "
        "unlinked_attribution qa_gates row; graph_def reseed is a no-op "
        "(re-apply the previous graph_def migration to fully revert)."
    )
