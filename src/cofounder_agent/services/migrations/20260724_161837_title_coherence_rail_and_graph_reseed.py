"""Migration 20260724_161837_title_coherence_rail_and_graph_reseed: add the
qa.title_coherence advisory rail to canonical_blog.

ISSUE: draft-grounded titling (2026-07-24). Titles were generated from the
TOPIC label only (the ``seo.generate_title`` template never rendered the
draft), so an ambiguous topic ("The Console Transition") shipped a
gaming-hardware title over an operator-console essay (task 1149dfc8) and a
directive topic shipped verbatim as the seo_title (task 1afabaf9). The
generation-side fixes ground the title prompts in the draft; this migration
wires the detection layer — an advisory title↔body LLM-verdict rail — into
the live graph:

1. Insert the ``title_coherence`` qa_gates row (advisory:
   ``required_to_pass=false``) so the rail's advisory status is DB-driven
   and graduation is a settings flip. Idempotent: fixed UUID + ``ON CONFLICT
   (id) DO NOTHING``; no-op on fresh installs where the baseline seeds it.
2. Re-seed the ``canonical_blog`` graph_def from the Python spec so the new
   ``qa_title_coherence`` node (after ``qa_content_originality``, before
   ``qa_web_factcheck``) reaches prod's stored row — the baseline runs once,
   so existing installs never re-read it. Writes the RAW spec (no per-node
   ``_contract_fp``); the boot self-heal ``ensure_active_graph_defs_stamped``
   re-stamps contract fingerprints on the same boot (poindexter#755).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Fixed id so the migration INSERT and the baseline seed target the same row.
_GATE_ID = "1bd92ea0-23eb-4102-8889-d03c1f019660"


async def up(pool) -> None:
    """Insert the title_coherence qa_gates row + re-seed canonical_blog."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        gate_tag = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES
                ($1, 'title_coherence', 'qa', 317, 'title_coherence',
                 false, true, '{}'::jsonb,
                 '{"atom": "qa.title_coherence", "rail": "title_coherence",
                   "description": "Title-vs-body honesty check — LLM verdict on whether the display title represents the article (wrong-domain titles, directive/assignment-label leaks, generic mush; the 2026-07-24 task-1149dfc8/1afabaf9 class). Advisory-first: scores but does not veto until graduated."}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
        )
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 7, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info(
        "title_coherence_rail_and_graph_reseed up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The stored graph_def is re-stamped by the boot self-heal and reverting the
    node would only re-open the undetected-title-mismatch gap; the qa_gates
    row is advisory and harmless. Nothing safe or useful to roll back to.
    """
    logger.info("title_coherence_rail_and_graph_reseed down: no-op (one-way reseed)")
