"""Migration 20260915_013131: graduate unlinked_attribution to a required QA gate.

ISSUE: Glad-Labs/poindexter#1052 (the fabrication classes the 2026-09-14
approval-queue review caught by hand)

Three phantom sources reached ``awaiting_approval`` at QA 97 that week — "the
VRLA Tech piece", "the AiCybr writeup", "according to the breakdown at Tutorials
Point" — and a fourth, unlinked-but-real source ("per a recent LinkedIn
analysis") went out without its link. ``qa.unlinked_attribution`` was blind to
every one of those shapes (its frames are widened in the same change), and it
was seeded ADVISORY (poindexter#765: score, never veto), so even a hit could not
have stopped the draft.

With the frames fixed, the rail is precise enough to gate: an attribution whose
subject matches no research-corpus source and carries no link is a fabricated
citation until proven otherwise, and the QA rescue cycle gets the offender list
in ``qa_feedback`` to link or drop it before the terminal reject. Flip is
DB-only (``required_to_pass``); the poindexter#454 lever still lets an operator
demote it.

Runs after the baseline on fresh installs too, so a new install gets the hard
gate without a hand step.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_GATE = "unlinked_attribution"


async def up(pool) -> None:
    """``qa_gates.unlinked_attribution.required_to_pass`` → true (idempotent)."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = true,
                   updated_at = now()
             WHERE name = $1
               AND required_to_pass IS DISTINCT FROM true
            """,
            _GATE,
        )
    logger.info(
        "Migration graduate_unlinked_attribution_to_a_required_qa_gate: applied (%s)",
        result,
    )


async def down(pool) -> None:
    """Back to advisory (the poindexter#765 posture)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE qa_gates SET required_to_pass = false, updated_at = now() WHERE name = $1",
            _GATE,
        )
    logger.info("Migration graduate_unlinked_attribution_to_a_required_qa_gate: reverted")
