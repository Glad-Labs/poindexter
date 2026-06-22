"""Migration 20260622_201603_demote_deepeval_faithfulness_to_advisory.

Self-heal-before-paging (docs/superpowers/specs/2026-06-22-qa-self-heal-before-paging-design.md
§6). ``deepeval_faithfulness`` was graduated to a hard gate by
``20260607_182804_graduate_eval_rails_to_required`` (#454), but it is a noisy
judge: in the originating 30-day review, faithfulness false-positives were a
meaningful share of the ~60-70% of written drafts the gate discarded (avg
rejected score 79). A hard faithfulness veto throws away a finished draft over a
single LLM judge's call. Demote it back to advisory
(``required_to_pass = false``) so it scores + surfaces on the QA Rails dashboard
but never halts the pipeline or blocks publication; the operator (and the two
retained hard gates — ``programmatic_validator`` + ``llm_critic``) decide.

Scope: faithfulness ONLY. The other three #454-graduated rails
(``deepeval_brand_fabrication`` / ``deepeval_g_eval`` / ``ragas_eval``) are left
as they are. This migration carries the latest timestamp, so it runs after #454
and wins on fresh DBs too — the baseline already seeds faithfulness advisory, so
a fresh DB now ends advisory (baseline false → #454 true → this false).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Demote ``deepeval_faithfulness`` to advisory (idempotent)."""
    async with pool.acquire() as conn:
        # asyncpg.execute() returns a status tag like "UPDATE N"; the
        # required_to_pass=TRUE guard makes a re-run a no-op ("UPDATE 0").
        status = await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = false,
                   updated_at       = NOW()
             WHERE name = 'deepeval_faithfulness'
               AND required_to_pass = true
            """,
        )
    logger.info(
        "Migration demote_deepeval_faithfulness_to_advisory: "
        "set required_to_pass=false on deepeval_faithfulness (%s)",
        status,
    )


async def down(pool) -> None:
    """Re-graduate ``deepeval_faithfulness`` to a hard gate (revert)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = true,
                   updated_at       = NOW()
             WHERE name = 'deepeval_faithfulness'
            """,
        )
    logger.info(
        "Migration demote_deepeval_faithfulness_to_advisory down: "
        "reset required_to_pass=true on deepeval_faithfulness",
    )
