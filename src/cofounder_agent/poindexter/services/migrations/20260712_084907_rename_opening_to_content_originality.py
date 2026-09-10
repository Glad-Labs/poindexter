"""Migration 20260712_084907_rename_opening_to_content_originality: rename the
opening_originality QA rail to content_originality.

ISSUE: RAG self-echo hardening — content_originality rail (Workstream 2 of
docs/superpowers/specs/2026-07-12-rag-self-echo-hardening-design.md §2.1).

The ``qa.opening_originality`` rail was renamed to ``qa.content_originality``
(and broadened from opening-only to a whole-post chunked scan). Renaming the
graph_def node id means prod's stored ``pipeline_templates.canonical_blog``
graph_def references an atom name that no longer exists in the registry — the
load-time drift gate (``assert_graph_def_current``) would halt every pipeline
run on the next worker boot (the #1876 failure class). This migration performs
the prod DATA transition the baseline seeds cannot:

1. Rename the live ``qa_gates`` row. Seeds use ``ON CONFLICT DO NOTHING`` and
   never rename an existing row, so prod's row lingers under the old name. No-op
   on fresh installs — the baseline already seeds ``content_originality``.
2. Re-seed the ``canonical_blog`` graph_def from the renamed Python spec so the
   stored node/atom names match the registry. Writes the RAW spec (no per-node
   ``_contract_fp``); the boot self-heal ``ensure_active_graph_defs_stamped``
   re-stamps contract fingerprints on the next worker boot (poindexter#755).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Rename the qa_gates row + re-seed the canonical_blog graph_def."""
    from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        # 1. Rename the live prod qa_gates row (idempotent: the WHERE clause
        #    matches nothing once already renamed, and nothing on fresh installs
        #    where the baseline seeded 'content_originality'). Merge the atom /
        #    rail / description keys onto the existing metadata via `||` so any
        #    other keys are preserved.
        gate_tag = await conn.execute(
            """
            UPDATE qa_gates
               SET name = 'content_originality',
                   reviewer = 'content_originality',
                   metadata = metadata || jsonb_build_object(
                       'atom', 'qa.content_originality',
                       'rail', 'content_originality',
                       'description',
                       'RAG self-echo net — flags a draft whose CONTENT '
                       'near-duplicates an existing published post (whole-post '
                       'chunked scan; the 2026-06 VRAM-hook cluster). '
                       'Advisory-first: scores but does not veto until graduated.'
                   )
             WHERE name = 'opening_originality'
            """
        )
        # 2. Re-seed the canonical_blog graph_def so the stored node/atom names
        #    match the renamed registry atom. Raw spec — boot self-heal stamps.
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, updated_at = now() "
            "WHERE slug = 'canonical_blog'",
            raw,
        )
    logger.info(
        "rename_opening_to_content_originality up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The boot self-heal owns fingerprint stamping, and the old
    ``qa.opening_originality`` atom no longer exists in the registry, so
    reverting the node rename would re-break prod on the next boot. There is
    nothing safe to roll back to.
    """
    logger.info("rename_opening_to_content_originality down: no-op (one-way reseed)")
