"""Reconcile the dormant ragas master flag with its disabled qa_gates row.

WHY (audit finding M6, 2026-06-02): ``app_settings.ragas_enabled`` is seeded
``true`` but ``qa_gates.ragas_eval.enabled`` is ``false`` — a contradiction.
Live ``qa_gates`` shows ``ragas_eval`` with 0 lifetime runs (dormant): it's
advisory (``required_to_pass=false``), only fires when ``research_sources`` is
non-empty (rare on the live path), and costs ~6K judge tokens when it does.
The conflicting master flag makes dashboards imply ragas coverage that isn't
running.

This flips ``ragas_enabled`` → ``false`` to match the already-disabled gate
row — a single, consistent "off". Behaviour-neutral (the rail was dormant
either way); it just stops the contradiction and the implied coverage. Re-enable
by flipping BOTH back and ensuring the pipeline supplies research_sources.

Idempotent UPDATE; no-op on a fresh install where the row isn't seeded yet
(baseline seeds it and runs first).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'false' WHERE key = 'ragas_enabled'"
        )
    logger.info(
        "reconcile_dormant_ragas_flag: applied (ragas_enabled=false — matches "
        "the disabled qa_gates.ragas_eval row; rail was dormant)"
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE app_settings SET value = 'true' WHERE key = 'ragas_enabled'"
        )
    logger.info("reconcile_dormant_ragas_flag: down — ragas_enabled restored to true")
