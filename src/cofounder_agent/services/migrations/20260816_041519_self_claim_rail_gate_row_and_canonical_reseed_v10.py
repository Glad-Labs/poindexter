"""Migration 20260816_041519: qa.self_claim rail — gate row + canonical reseed v10.

ISSUE: Glad-Labs/poindexter#1007

When a draft describes Poindexter's own internals, nothing checked the
description against reality — three fabricated/stale self-claims reached
``awaiting_approval`` at Q94–95 (an invented retrieval mechanism, invented
quality scores, a version two releases stale). The new ``qa.self_claim``
advisory rail verifies the deterministic subset: version strings vs the
running package, quality-score claims vs ``pipeline_tasks``, backticked
settings keys vs ``app_settings``, package file paths vs the tree.

Same two-step shape as the title_coherence precedent (20260724_161837):

1. Insert the ``self_claim`` qa_gates row (advisory:
   ``required_to_pass=false``). Idempotent: fixed UUID + ``ON CONFLICT (id)
   DO NOTHING``; no-op on fresh installs where the baseline seeds it.
2. Re-seed the ``canonical_blog`` graph_def from the Python spec (v10) so
   the new ``qa_self_claim`` node (after ``qa_title_coherence``, before
   ``qa_web_factcheck``) reaches prod's stored row. Writes the RAW spec;
   the boot self-heal ``ensure_active_graph_defs_stamped`` re-stamps
   contract fingerprints on the same boot (poindexter#755).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Fixed id so the migration INSERT and the baseline seed target the same row.
_GATE_ID = "e2438d09-10d0-47ba-8d92-a7075fe977fe"


async def up(pool) -> None:
    """Insert the self_claim qa_gates row + re-seed canonical_blog (v10)."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        gate_tag = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES
                ($1, 'self_claim', 'qa', 318, 'self_claim',
                 false, true, '{}'::jsonb,
                 '{"atom": "qa.self_claim", "rail": "self_claim",
                   "description": "Deterministic verification of the draft''s claims about our own system — version strings vs pyproject, quality-score claims vs pipeline_tasks, backticked settings keys vs app_settings, package file paths vs the tree (poindexter#1007: 3 fabricated/stale self-claims reached awaiting_approval at Q94-95). Advisory-first: scores + surfaces offenders but does not veto until graduated."}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
        )
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 10, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info(
        "self_claim_rail_gate_row_and_canonical_reseed_v10 up: qa_gates=%s "
        "graph_def=%s", gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The stored graph_def is re-stamped by the boot self-heal, and reverting
    the node would only re-open the unchecked-self-claim gap; the qa_gates
    row is advisory and harmless. Nothing safe or useful to roll back to
    (same posture as the title_coherence reseed).
    """
    logger.info(
        "self_claim_rail_gate_row_and_canonical_reseed_v10 down: no-op "
        "(one-way reseed)"
    )
