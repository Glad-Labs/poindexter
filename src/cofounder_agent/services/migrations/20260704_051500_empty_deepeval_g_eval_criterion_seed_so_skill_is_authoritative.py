"""Migration 20260704_051500: empty the ``deepeval_g_eval_criterion`` seed
so the SKILL.md catalog default is authoritative (poindexter#830).

#829 added ``qa.deepeval_g_eval_criterion`` to the SKILL.md catalog and made
``evaluate_g_eval`` resolve it when no criterion is passed. But the baseline
still seeded ``app_settings.deepeval_g_eval_criterion`` with the full rubric
text, and ``multi_model_qa._check_deepeval_g_eval`` uses any *non-empty*
setting value — so the seeded row permanently shadowed the new catalog key on
every seeded install (the same masking trap the project fought with Langfuse
production-label overrides). Editing the skill — the documented edit path —
would not have changed production.

This empties the seeded default to the ``''`` unset sentinel so the setting
becomes an OPTIONAL operator override: empty → ``multi_model_qa`` passes
``criterion=None`` → the SKILL.md catalog default is used; a non-empty value
still wins (tune-without-code preserved).

Convergence step, same posture as ``20260622_200222_drop_pipeline_tasks_category``:

  * Fresh installs — the baseline now seeds ``''`` directly, so the value-guard
    below misses and this no-ops.
  * Existing installs (prod carries the full rubric from the pre-#830 seed) —
    this empties it, but ONLY where the value still equals the historical
    baked-in default, so a real operator override is preserved untouched.

Behavior-preserving today: the historical seed value is byte-identical to the
catalog default, so the g-eval judge grades against the exact same rubric
before and after — only the *source* of that rubric changes (seed → catalog),
which is what lets future skill edits take effect.

The historical value is frozen as a literal here on purpose: the migration
converges rows that still carry the ORIGINAL default, independent of any later
edit to the catalog / ``_DEFAULT_G_EVAL_CRITERION`` constant.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The rubric text the pre-#830 baseline seeded (== _DEFAULT_G_EVAL_CRITERION at
# the time of this migration). Frozen literal: we only converge rows that still
# hold this exact original default, never an operator's customized value.
_HISTORICAL_DEFAULT = (
    "The output is well-grounded in the input topic, internally consistent "
    "across paragraphs, and does not invent specific facts, names, "
    "statistics, or quotes that lack support."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE app_settings
               SET value = ''
             WHERE key = 'deepeval_g_eval_criterion'
               AND value = $1
            """,
            _HISTORICAL_DEFAULT,
        )
    logger.info(
        "empty_deepeval_g_eval_criterion_seed up: %s "
        "(UPDATE 0 = fresh install already seeded empty, or operator override "
        "present — both left as-is)",
        status,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE app_settings
               SET value = $1
             WHERE key = 'deepeval_g_eval_criterion'
               AND value = ''
            """,
            _HISTORICAL_DEFAULT,
        )
    logger.info("empty_deepeval_g_eval_criterion_seed down: %s", status)
