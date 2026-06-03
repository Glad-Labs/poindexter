"""Migration 20260603_193523_re_enable_native_guardrails_rails: re-enable native guardrails rails

ISSUE: Glad-Labs/glad-labs-stack#996

Background — what is this migration for? Why is it being added?

On 2026-06-03 the migration ``20260603_010500_disable_dead_guardrails_rails``
turned the two guardrails rails OFF (``guardrails_enabled=false`` +
``qa_gates.guardrails_brand`` / ``guardrails_competitor`` ``enabled=false``)
because ``guardrails-ai`` had been uninstalled (PyPI quarantine after the
CVE-2026-45758 supply-chain compromise) and the rails were fail-open no-ops.

``services/guardrails_rails.py`` has since been **reimplemented natively**
with no third-party dependency — the brand rail runs ``content_validator``'s
fabrication patterns directly and the competitor rail is a ``re``
word-boundary regex over the operator CSV. The rails are real again, so this
migration REVERSES the disable:

- ``app_settings.guardrails_enabled`` → ``true`` (master flag back on).
- ``qa_gates.guardrails_brand`` / ``guardrails_competitor`` → ``enabled=true``.

The rails stay **ADVISORY** (``required_to_pass=false``), matching the other
OSS QA rails (DeepEval ×3, Ragas) — they contribute to the weighted QA score
but don't hard-veto a post. We are NOT graduating them to a hard gate here.
``required_to_pass`` is set explicitly (not just left alone) so the advisory
posture is asserted regardless of any prior drift.

Idempotent UPDATEs; rows are baseline-seeded so this no-ops on a fresh
install where the disable migration already restored them (the baseline runs
first, then both timestamped migrations in order).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration — turn the native guardrails rails back on (advisory)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'true' WHERE key = 'guardrails_enabled'"
        )
        await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = true,
                   required_to_pass = false,
                   updated_at = NOW()
             WHERE name IN ('guardrails_brand', 'guardrails_competitor')
            """
        )
    logger.info(
        "re_enable_native_guardrails_rails: applied (guardrails_enabled=true; "
        "guardrails_brand/competitor qa_gates enabled + advisory — native "
        "reimplementation, no guardrails-ai dep)"
    )


async def down(pool) -> None:
    """Revert — re-disable the rails, restoring the prior dead-rail posture."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'false' WHERE key = 'guardrails_enabled'"
        )
        await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = false, updated_at = NOW()
             WHERE name IN ('guardrails_brand', 'guardrails_competitor')
            """
        )
    logger.info(
        "re_enable_native_guardrails_rails down: reverted "
        "(guardrails rails disabled again)"
    )
