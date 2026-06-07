"""Migration: graduate advisory eval rails to required_to_pass=True.

Closes Glad-Labs/poindexter#454.

The four eval rails seeded by Lane D (#329) — DeepEval
brand_fabrication / g_eval / faithfulness and Ragas ragas_eval — have
been running in advisory mode since they were wired up in the Lane D
close-out.  Advisory means a rail rejection becomes a soft warning that
surfaces on the QA Rails Grafana dashboard but does NOT halt the
pipeline or block publication.

This migration graduates all four to hard gates
(required_to_pass=True), matching the two existing hard gates
(programmatic_validator + llm_critic).  After this migration a
deepeval or ragas rejection will stop the content generation run and
surface a rejection event on the Findings dashboard.

Guardrails rails are intentionally excluded: guardrails-ai was
uninstalled (migration 20260603_010500 sets guardrails_enabled=false)
and the library is not available in the worker image.  Graduating
guardrails rails while the library is absent would create misleading
hard gates that always pass vacuously.

Rollback: resets the four rails to advisory (required_to_pass=False).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The four eval rails graduating from advisory → hard gate.
# Guardrails rails are deliberately excluded (library uninstalled).
_EVAL_RAILS = [
    "deepeval_brand_fabrication",
    "deepeval_g_eval",
    "deepeval_faithfulness",
    "ragas_eval",
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # asyncpg.execute() returns a status tag like "UPDATE N".
        status = await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = TRUE,
                   updated_at       = NOW()
             WHERE name = ANY($1::text[])
               AND required_to_pass = FALSE
            """,
            _EVAL_RAILS,
        )
    logger.info(
        "Migration graduate_eval_rails_to_required: "
        "set required_to_pass=TRUE on rails %s (%s)",
        ", ".join(_EVAL_RAILS),
        status,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = FALSE,
                   updated_at       = NOW()
             WHERE name = ANY($1::text[])
            """,
            _EVAL_RAILS,
        )
    logger.info(
        "Migration graduate_eval_rails_to_required down: "
        "reset required_to_pass=FALSE on %d rail(s)",
        len(_EVAL_RAILS),
    )
