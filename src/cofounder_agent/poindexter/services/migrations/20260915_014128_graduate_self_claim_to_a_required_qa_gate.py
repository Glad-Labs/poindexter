"""Migration: graduate self_claim to a required QA gate.

ISSUE: Glad-Labs/poindexter#1052

Two drafts in the 2026-09-14 approval queue invented first-person claims
about this install at QA 95-97 — "We also run Jettison" (a product that
does not exist here; the name came from a contrast drawn in one of our own
published posts) and an internal engine audit "on a 5090 with 128GB of
system RAM" (the host has 64). ``qa.self_claim`` was advisory (poindexter#1007
shipped it that way on purpose) and had no operating record to check named
capabilities or install specs against. Both gaps close in the same change:
the rail gains a capability layer and an install-spec layer over
``services/operating_record.py``, and this migration makes its verdict gate.

Precision is protected by construction — every check runs only in
our-own-system context, a fact the record cannot derive is skipped rather
than guessed, and the poindexter#454 lever (``required_to_pass``) demotes the
rail with no deploy. Runs after the baseline on fresh installs too.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_GATE = "self_claim"


async def up(pool) -> None:
    """``qa_gates.self_claim.required_to_pass`` → true (idempotent)."""
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
    logger.info("Migration graduate_self_claim_to_a_required_qa_gate: applied (%s)", result)


async def down(pool) -> None:
    """Back to advisory (the poindexter#1007 posture)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE qa_gates SET required_to_pass = false, updated_at = now() WHERE name = $1",
            _GATE,
        )
    logger.info("Migration graduate_self_claim_to_a_required_qa_gate: reverted")
