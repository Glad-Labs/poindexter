"""Migration 20260612_010000: seed reasoning_token_leak content_validator_rules row.

ISSUE: Glad-Labs/glad-labs-stack#1283

Background — a draft whose body contained ``<|channel>thought<channel|>…``
control tokens scored 91 and reached ``awaiting_approval`` because the
programmatic validator had no rule for leaked reasoning tokens. The
generation-boundary stripper in ``services/llm_providers/thinking_models.py``
already removes these before persistence, but the validator layer is
defence-in-depth for cases where the stripper is bypassed (e.g. a JSON-mode
helper that skips it).

This migration registers the new ``reasoning_token_leak`` rule (critical
severity, enabled by default, applies to ALL niches) in the
``content_validator_rules`` table so operators can flip it on/off, or
scope it to specific niches, via the DB without a code change.

Safe to re-run: ``ON CONFLICT (id) DO NOTHING``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Stable UUID for this rule row.
_RULE_ID = "b3e1a7c2-4f58-4d90-9e6a-2c8f0d3a5b71"


async def up(pool) -> None:
    """Insert the reasoning_token_leak rule into content_validator_rules."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO content_validator_rules
                (id, name, enabled, severity, threshold, applies_to_niches, description)
            VALUES
                ($1::uuid, 'reasoning_token_leak', true, 'error', '{}'::jsonb, NULL,
                 'Detects leaked reasoning/chat-template control tokens in published '
                 'prose (<|channel>, <|im_start|>, <think>, </think>, etc.). Critical '
                 '— a draft whose body contains these tokens almost certainly had its '
                 'entire article inside a reasoning channel. Defence-in-depth for the '
                 'generation-boundary stripper in thinking_models.strip_reasoning_artifacts.')
            ON CONFLICT (id) DO NOTHING
            """,
            _RULE_ID,
        )
    logger.info(
        "Migration seed_reasoning_token_leak_validator_rule up: "
        "inserted reasoning_token_leak rule (id=%s).",
        _RULE_ID,
    )


async def down(pool) -> None:
    """Remove the reasoning_token_leak rule row (soft-delete by disabling)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE content_validator_rules
               SET enabled = false
             WHERE id = $1::uuid
            """,
            _RULE_ID,
        )
    logger.info(
        "Migration seed_reasoning_token_leak_validator_rule down: "
        "disabled reasoning_token_leak rule (id=%s).",
        _RULE_ID,
    )
