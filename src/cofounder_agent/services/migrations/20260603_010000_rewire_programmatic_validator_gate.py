"""Re-wire the programmatic anti-hallucination validator into canonical_blog
(graph_def version 4) + reconcile the url_verifier gate flag with reality.

WHY (audit finding C1, 2026-06-02): the #355 atom-cutover replaced the
monolithic ``cross_model_qa`` stage — which ran ``programmatic_validator`` →
``url_verifier`` → ``llm_critic`` as a hard-gate triplet — with the ``qa.*``
atom chain, but only ported ``llm_critic`` (→ ``qa.critic``). The ``atom_runs``
table for the live graph_def path (2026-06-02) shows the qa block running only
``qa.critic / qa.deepeval / qa.guardrails / qa.ragas / qa.aggregate`` — the
deterministic regex/heuristic fabrication net (fake people, fake stats, made-up
Glad Labs claims, hallucinated library refs) silently stopped gating, even
though ``qa_gates.programmatic_validator.required_to_pass`` is still ``true``.

This migration:

1. Re-seeds ``pipeline_templates.graph_def`` (canonical_blog) from the updated
   authoritative spec, which now has a ``qa.programmatic`` rail FIRST in the qa
   block (caption_images → qa_programmatic → qa_critic → …). On a critical
   fabrication that rail emits a non-advisory failing review and qa.aggregate
   vetoes + persists the reject — restoring the hard programmatic gate. Version
   bumps 3 → 4.

2. Makes the ``programmatic_validator`` qa_gates row explicit (enabled=true,
   required_to_pass=true) so the restored gate is a real veto on fresh installs
   too (idempotent — prod already has these values).

3. Reconciles the ``url_verifier`` qa_gates row: sets ``required_to_pass=false``
   (advisory). The live path has NO ``qa.url_verifier`` atom — the only URL
   check is ``stage.url_validation``, which is DELIBERATELY non-halting ("a
   broken link doesn't block publishing"). The row claimed "dead links block
   publish" (required_to_pass=true) but nothing enforced it. This is a pure
   truth-in-advertising fix — it changes NO runtime behavior (the gate never
   actually halted), it just stops the dashboard/DB implying a gate that does
   not run. Wiring a genuinely-halting URL gate is a separate opt-in.

Imports only ``canonical_blog_spec`` (pure data — no LangGraph) + stdlib so it
stays light for the migrations-smoke env.
"""
from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    payload = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        # 1. Re-seed the active graph_def from the authoritative spec.
        await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $1::jsonb, version = 4, updated_at = NOW()
             WHERE slug = 'canonical_blog'
            """,
            payload,
        )
        # 2. The restored programmatic gate must be a real veto.
        await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = true, required_to_pass = true, updated_at = NOW()
             WHERE name = 'programmatic_validator'
            """
        )
        # 3. url_verifier: demote to advisory to match the non-halting
        #    stage.url_validation (no qa.url_verifier atom exists). Behaviour-
        #    neutral — it never halted — but the DB now tells the truth.
        await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = false, updated_at = NOW()
             WHERE name = 'url_verifier'
            """
        )
    logger.info(
        "rewire_programmatic_validator_gate: applied (graph_def v4; "
        "programmatic_validator=hard-gate; url_verifier=advisory)"
    )


async def down(pool) -> None:
    # Forward-only for the graph_def reseed; restore the url_verifier flag so a
    # rollback returns the qa_gates row to its prior (mis-advertised) state.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = true, updated_at = NOW()
             WHERE name = 'url_verifier'
            """
        )
    logger.info(
        "rewire_programmatic_validator_gate: down — url_verifier restored to "
        "required_to_pass=true (graph_def reseed is forward-only)"
    )
