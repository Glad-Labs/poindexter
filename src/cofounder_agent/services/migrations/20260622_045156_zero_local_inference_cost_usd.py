"""Migration 20260622_045156_zero_local_inference_cost_usd: zero local inference cost_usd

Cost-control attribution P1 (docs/superpowers/specs/2026-06-21-cost-control-attribution-design.md).

History backfill for the P1 write invariant: a LOCAL inference/media row must
have ``cost_usd = 0`` on the API axis (electricity is tracked via
``electricity_kwh`` + the brain's measured PSU rows). Before the invariant,
local calls carried two kinds of bogus dollars: phantom hosted prices (a bare
Ollama tag like ``llama3.2:3b`` collided with a hosted price in
``litellm.model_cost`` — the 2026-06-21 incident) and per-call electricity
estimates that double-counted the brain's measurement. This zeroes both on
historical local rows so ``cost_ledger.get_spend``'s api axis reads honest
numbers. ``electricity_kwh`` / tokens / model are preserved (the P5 savings
view needs them); genuinely-paid cloud rows and the brain's electricity rows
are untouched.

One-way (down() is a no-op): reverting would re-introduce the phantom dollars.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Selects historical LOCAL cost rows to zero: non-electricity, currently
# carrying dollars, on a non-cloud provider, and either an explicitly-local
# provider OR a model whose namespace isn't a known cloud prefix. Hardcoded
# literal (no user input) — hence ``# nosec B608`` on the callers.
_LOCAL_PREDICATE = (
    "COALESCE(cost_type,'inference') NOT LIKE 'electricity%' "
    "AND cost_usd > 0 "
    "AND provider NOT IN ('anthropic','openai','gemini','openrouter') "
    "AND (provider IN ('ollama','ollama_native','litellm') "
    "     OR model !~ '^(anthropic|openai|gemini|openrouter)/')"
)


async def up(pool) -> None:
    """Zero cost_usd on historical local rows (idempotent: re-run zeroes nothing new)."""
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            f"SELECT COUNT(*) FROM cost_logs WHERE {_LOCAL_PREDICATE}"  # nosec B608
        )
        logger.info(
            "Migration zero_local_inference_cost_usd: zeroing cost_usd on %s "
            "historical local rows (phantom + per-call electricity dollars)",
            n,
        )
        await conn.execute(
            f"UPDATE cost_logs SET cost_usd = 0 WHERE {_LOCAL_PREDICATE}"  # nosec B608
        )
    logger.info("Migration zero_local_inference_cost_usd: applied")


async def down(pool) -> None:
    """One-way migration — documented no-op.

    Reverting would re-introduce the phantom hosted prices + the double-counted
    per-call electricity dollars this removes. Forward-only.
    """
    return None
