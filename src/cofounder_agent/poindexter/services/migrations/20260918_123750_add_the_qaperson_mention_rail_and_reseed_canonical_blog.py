"""Migration 20260918_123750_add_the_qaperson_mention_rail_and_reseed_canonical_blog: wire the
qa.person_mention advisory rail into canonical_blog.

ISSUE poindexter#1009. Every content rail we had asks whether the draft is
TRUE. On 2026-08-09 a draft reached ``awaiting_approval`` at Q94 having named
a private individual — surfaced by web research purely because they share a
surname with the product — and characterised how they do their job from a
rating site. The claims were verified accurate, which is exactly why no
fabrication, citation, or fact-check rail could fire. Only the human approval
gate caught it (the name was removed before publish).

1. Insert the ``person_mention`` qa_gates row, advisory
   (``required_to_pass=false``), so graduation is a settings flip rather than
   a deploy. Idempotent: fixed UUID + ``ON CONFLICT (id) DO NOTHING``, a no-op
   on fresh installs where the baseline seeds it.
2. Re-seed the ``canonical_blog`` graph_def from the Python spec so the new
   ``qa_person_mention`` node (after ``qa_unlinked_attribution``, before
   ``qa_consistency``) reaches prod's stored row — the baseline runs once, so
   existing installs never re-read it. Writes the RAW spec (no per-node
   ``_contract_fp``); the boot self-heal ``ensure_active_graph_defs_stamped``
   re-stamps contract fingerprints on the same boot (poindexter#755).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Fixed id so this INSERT and any future baseline seed target the same row.
_GATE_ID = "626496b4-ba4d-5f56-939f-3c5be5b54c36"

_DESCRIPTION = (
    "Should this named human be in the post at all? Deterministic check for "
    "rating/review-site data attached to a named person, plus a two-stage "
    "local-LLM pass classifying each extracted name as a public figure in "
    "public capacity or a private individual (the 2026-08-09 task-68252b36 "
    "class). Advisory-first: scores but does not veto until graduated."
)


async def up(pool) -> None:
    """Insert the person_mention qa_gates row + re-seed canonical_blog."""
    from poindexter.services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    metadata = json.dumps({
        "atom": "qa.person_mention",
        "rail": "person_mention",
        "description": _DESCRIPTION,
    })
    async with pool.acquire() as conn:
        gate_tag = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES
                ($1, 'person_mention', 'qa', 318, 'person_mention',
                 false, true, '{}'::jsonb, $2::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
            metadata,
        )
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 14, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info(
        "person_mention_rail_and_graph_reseed up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The stored graph_def is re-stamped by the boot self-heal, and reverting the
    node would only re-open the gap where a named private individual has
    nothing but the human approval gate between it and publication. The
    qa_gates row is advisory and harmless.
    """
    logger.info("person_mention_rail_and_graph_reseed down: no-op (one-way reseed)")
