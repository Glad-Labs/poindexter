"""Migration 20260704_180500: demote ``deepeval_brand_fabrication`` to advisory.

The ``deepeval_brand_fabrication`` QA gate was seeded ``required_to_pass=true``
(one of three nominal hard gates), but ``services/deepeval_rails.py::
evaluate_brand_fabrication`` fail-OPENS to a passing ``(True, 1.0)`` whenever the
``deepeval`` package is missing or throws — so as a "hard" gate it silently
scored a perfect PASS on every run when deepeval was unhealthy, with no visible
signal (glad-labs-stack#2126). Per the 2026-07-04 hidden-failures audit the
operator wants exactly two hard rejectors — ``programmatic_validator`` and
``llm_critic`` — and everything else advisory-but-visible.

**Why this migration exists.** The Phase F baseline seeds ``qa_gates`` with
``ON CONFLICT (id) DO NOTHING``, so editing the baseline seed row only affects
FRESH installs. Existing installs (prod) already carry the row with
``required_to_pass=true`` and the seed no-ops on them. This is the convergence
step:

  * Fresh installs — the edited ``0000_baseline.seeds.sql`` already seeds
    ``required_to_pass=false``; this UPDATE is a harmless no-op.
  * Existing installs (prod) — this performs the real demotion.

Both converge to an advisory ``deepeval_brand_fabrication``. Idempotent
(``WHERE required_to_pass IS DISTINCT FROM false``), so re-running is a no-op.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE qa_gates
               SET required_to_pass = false
             WHERE name = 'deepeval_brand_fabrication'
               AND required_to_pass IS DISTINCT FROM false
            """
        )
    logger.info(
        "demote_deepeval_brand_fabrication_advisory up: %s "
        "(no-op where already advisory)",
        result,
    )


async def down(pool) -> None:
    # Refuse to re-arm a fail-open gate as a hard rejector. The rail returns a
    # passing score when deepeval is missing/errors, so promoting it back to
    # required_to_pass=true would re-introduce the silent-pass hazard #2126
    # closed. If a future change makes the rail fail CLOSED, re-arm it in a
    # fresh forward migration, not by reverting this one.
    logger.info(
        "demote_deepeval_brand_fabrication_advisory down: no-op "
        "(refusing to re-arm a fail-open rail as a hard gate)"
    )
