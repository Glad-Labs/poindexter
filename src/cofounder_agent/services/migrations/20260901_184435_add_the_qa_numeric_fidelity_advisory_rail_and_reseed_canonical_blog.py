"""Migration 20260901_184435_add_the_qa_numeric_fidelity_advisory_rail_and_reseed_canonical_blog:
add the qa.numeric_fidelity advisory rail to canonical_blog.

ISSUE: numeric fidelity rail (2026-09-01). Every anti-hallucination layer we
run is a judgement — a regex that recognises fabrication shapes, or an LLM
asked whether the draft is faithful. None of them is arithmetic, so a number
the draft attributes to a source is checked only by opinion. Measured over 40
published posts, one carried a *headline* statistic ("110,000 papers") that
appears nowhere in the research corpus it was written from, and every rail
passed it.

This migration wires the detection layer into the live graph:

1. Insert the ``numeric_fidelity`` qa_gates row (advisory:
   ``required_to_pass=false``, ``execution_order=155`` — between
   ``citation_verifier`` at 150 and ``unlinked_attribution`` at 160) so the
   rail's advisory status is DB-driven and graduation is a settings flip.
   Idempotent: fixed UUID + ``ON CONFLICT (id) DO NOTHING``; a no-op on fresh
   installs where the baseline seeds the same row.
2. Re-seed the ``canonical_blog`` graph_def from the Python spec so the new
   ``qa_numeric_fidelity`` node (after ``qa_citations``, before
   ``qa_unlinked_attribution``) reaches prod's stored row — the baseline runs
   once, so existing installs never re-read it. Writes the RAW spec (no
   per-node ``_contract_fp``); the boot self-heal
   ``ensure_active_graph_defs_stamped`` re-stamps contract fingerprints on the
   same boot (poindexter#755).

Version 11 → 12. Mirrors 20260724_161837 (the title_coherence rail).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Fixed id so the migration INSERT and the baseline seed target the same row.
_GATE_ID = "341eee75-38e3-46dc-9f93-c99e8648bad7"


async def up(pool) -> None:
    """Insert the numeric_fidelity qa_gates row + re-seed canonical_blog."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        gate_tag = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES
                ($1, 'numeric_fidelity', 'qa', 155, 'numeric_fidelity',
                 false, true, '{}'::jsonb,
                 '{"atom": "qa.numeric_fidelity", "rail": "numeric_fidelity",
                   "description": "Arithmetic check that every ATTRIBUTED number in the draft reconciles with a number in the research corpus, at the precision the author wrote (or via a recorded derivation). No LLM. Attribution-gated: scoring every number flagged 33% of ordinary prose, all false positives. Advisory-first: scores but does not veto until graduated."}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
        )
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 12, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info(
        "numeric_fidelity_rail_and_graph_reseed up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The stored graph_def is re-stamped by the boot self-heal, and reverting the
    node would only re-open the unsourced-statistic gap. The qa_gates row is
    advisory and harmless. Nothing safe or useful to roll back to.
    """
    logger.info("numeric_fidelity_rail_and_graph_reseed down: no-op (one-way reseed)")
