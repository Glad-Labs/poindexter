"""Migration 20260924_185336: disable the ragas_eval gate row while ragas_enabled is off.

ISSUE: Glad-Labs/poindexter#1065.

``ragas_enabled`` was turned off on 2026-09-15 as a stopgap: Ragas was crashing
the Prefect flow (poindexter#1053). An earlier version of this docstring said
it was deliberate to save judge tokens; that was a June episode, not this one.
Only the setting was flipped, though: the ``ragas_eval`` row in
``qa_gates`` stayed ``enabled``. With the switch off the ``qa.ragas`` atom
returns ``{}``, so the gate can never run. It is harmless while advisory, and a
landmine the moment anyone graduates it — ``missing_required_gates`` reads an
absent required rail as a veto and would hard-reject every post (the
poindexter#1060 shape).

The guardrails retirement (20260910_144500) turned off BOTH halves; this brings
ragas into line. Conditional on the switch actually being off, so an install
that runs ragas keeps its gate. Re-enabling is two steps and both are needed:
``ragas_enabled=true`` then ``poindexter qa-gates enable ragas_eval``.

The recurrence guard lives in code, not here: ``declarative_config_service``
now refuses ``required_to_pass=true`` on a gate whose master switch is off, and
``poindexter qa-gates list`` warns about every enabled gate in that state
(``services/qa_gates_db.RAIL_MASTER_SWITCHES``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_NOTE = (
    "Disabled 2026-09-24 (poindexter#1065): ragas_enabled=false makes qa.ragas "
    "produce no review, so an enabled gate row was inert and a graduation "
    "would have rejected every post. Turn ragas_enabled on before re-enabling."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        tag = await conn.execute(
            """
            UPDATE qa_gates
               SET enabled = false,
                   required_to_pass = false,
                   metadata = COALESCE(metadata, '{}'::jsonb)
                              || jsonb_build_object('disabled_note', $1::text)
             WHERE name = 'ragas_eval'
               AND enabled = true
               AND EXISTS (
                   SELECT 1 FROM app_settings
                    WHERE key = 'ragas_enabled'
                      AND lower(trim(value)) NOT IN ('true', '1', 'yes', 'on')
               )
            """,
            _NOTE,
        )
    logger.info("disable ragas_eval gate while ragas_enabled is off: %s", tag)


async def down(pool) -> None:
    # Not reversible by data: whether the row was enabled before is exactly
    # what `up` overwrote. Re-enable by hand once ragas_enabled is on.
    return None
