"""Make the guardrails-ai flag tell the truth — the library is uninstalled.

WHY (audit finding M1, 2026-06-02): ``guardrails-ai`` was dropped from
``pyproject.toml`` on 2026-05-12 (PyPI quarantine). The two guardrails rails
(``guardrails_brand`` / ``guardrails_competitor``) now catch the ImportError
and fail-OPEN to a clean pass — they screen nothing. But ``guardrails_enabled``
is seeded ``true`` and both ``qa_gates`` rows are ``enabled=true``, so the QA
Rails dashboard + Integrations panels show them as ACTIVE coverage that does
not exist. That is exactly the "advertised protection that's actually off"
class this audit set out to remove.

This migration reconciles the DB with reality:

- ``app_settings.guardrails_enabled`` → ``false`` (master flag).
- ``qa_gates.guardrails_brand`` / ``guardrails_competitor`` → ``enabled=false``.

Behaviour-neutral on the live path — the rails were already no-ops (advisory +
fail-open) — but the dashboards now stop implying a dead framework is gating
content. Re-enable by reinstalling guardrails-ai and flipping these back.

Idempotent UPDATEs; rows are baseline-seeded so no-op on a fresh install where
they don't exist yet (the baseline seeds them, and the baseline runs first).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
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
        "disable_dead_guardrails_rails: applied (guardrails_enabled=false; "
        "guardrails_brand/competitor qa_gates disabled — library uninstalled)"
    )


async def down(pool) -> None:
    # Restore the prior (mis-advertised) state on rollback.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'true' WHERE key = 'guardrails_enabled'"
        )
        await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = true, updated_at = NOW()
             WHERE name IN ('guardrails_brand', 'guardrails_competitor')
            """
        )
    logger.info("disable_dead_guardrails_rails: down — guardrails flags restored to true")
