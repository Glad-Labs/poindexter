"""Migration 20260915_015618_add_the_qa_freshness_rail_and_reseed_canonical_blog:
add the qa.freshness rail (stale news-take veto) to canonical_blog.

ISSUE: freshness rail (2026-09-15). The 2026-09-14 approval-queue review found
a reaction to OpenAI's September 8 announcement that reached
``awaiting_approval`` on the 11th reading "OpenAI put out a paper this week"
and was still there on the 14th. Every truth rail passed it — the claims were
right — because no rail knows what day it is. A stale news take is not a
rewrite problem (the words can be made current, the piece cannot), so the new
rail vetoes and its veto is deliberately non-rescuable.

This migration wires the rail into the live graph:

1. Insert the ``freshness`` qa_gates row as a HARD gate
   (``required_to_pass=true``, ``execution_order=319`` — after
   ``self_claim`` at 318, before ``web_factcheck`` at 500). Required from
   day one because the rail only speaks when a draft is news-shaped AND past
   ``qa_freshness_max_age_days`` — evergreen posts never see it, and a fresh
   news take gets an approving review. Demotion is the poindexter#454 lever
   (``required_to_pass=false``), no code change. Idempotent: fixed UUID +
   ``ON CONFLICT (id) DO NOTHING``.
2. Re-seed the ``canonical_blog`` graph_def from the Python spec so the new
   ``qa_freshness`` node (after ``qa_self_claim``, before
   ``qa_web_factcheck``) reaches prod's stored row — the baseline runs once,
   so existing installs never re-read it. Writes the RAW spec (no per-node
   ``_contract_fp``); the boot self-heal ``ensure_active_graph_defs_stamped``
   re-stamps contract fingerprints on the same boot (poindexter#755).

Version 12 → 13. Mirrors 20260901_184435 (the numeric_fidelity rail).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Fixed id so the migration INSERT and any later baseline seed target the same row.
_GATE_ID = "e4b5f381-f776-4111-b66a-b6b224275cb7"


async def up(pool) -> None:
    """Insert the freshness qa_gates row + re-seed canonical_blog."""
    from poindexter.services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

    raw = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        gate_tag = await conn.execute(
            """
            INSERT INTO qa_gates
                (id, name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES
                ($1, 'freshness', 'qa', 319, 'freshness',
                 true, true, '{}'::jsonb,
                 '{"atom": "qa.freshness", "rail": "freshness",
                   "description": "Stale news-take veto. A draft anchored to a moment (relative-time phrasing such as this week / yesterday / just announced) or sourced from a news feed must reach QA within qa_freshness_max_age_days of its newest dated source; older is vetoed and the veto is not rescuable, because a rewrite cannot make a late take current. Evergreen drafts get no review. No LLM."}'::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            _GATE_ID,
        )
        gd_tag = await conn.execute(
            "UPDATE pipeline_templates SET graph_def = $1::jsonb, "
            "version = 13, updated_at = now() "
            "WHERE slug = 'canonical_blog' AND active = true",
            raw,
        )
    logger.info(
        "qa_freshness_rail_and_graph_reseed up: qa_gates=%s graph_def=%s",
        gate_tag, gd_tag,
    )


async def down(pool) -> None:
    """One-way forward reseed — explicit no-op.

    The stored graph_def is re-stamped by the boot self-heal, and reverting the
    node would only re-open the stale-news gap. To silence the rail without a
    code change, demote the gate: ``UPDATE qa_gates SET required_to_pass=false
    WHERE name='freshness'`` (or ``qa_freshness_enabled=false``).
    """
    logger.info("qa_freshness_rail_and_graph_reseed down: no-op (one-way reseed)")
