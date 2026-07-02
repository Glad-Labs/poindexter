"""Migration 20260702_014630: rename the ``glad_labs_claim`` validator rule to ``company_claim``.

Mirror-hygiene follow-up to the #2022/#2027/#2030 operator-identity scrubs: the
``content_validator_rules`` row named ``glad_labs_claim`` was the last
operator-flavored identifier shipping in the public seeds. The rule itself is
already generic (it reads the configured ``company_name`` / ``company_age_months``
settings), so only the NAME changes — ``company_claim`` — in lockstep with the
code's ``_enabled("company_claim")`` lookup and the ``COMPANY_IMPOSSIBLE``
pattern constant (renamed from ``GLAD_LABS_IMPOSSIBLE``, alias retained).

Convergence step, same posture as ``20260622_200222_drop_pipeline_tasks_category``:

  * Fresh installs — the baseline seeds the row as ``company_claim`` directly,
    so the ``WHERE name = 'glad_labs_claim'`` clause misses and this no-ops.
  * Existing installs (prod carries the old name from the pre-rename seed) —
    this performs the real rename, preserving the row's operator tuning
    (enabled / severity / threshold / applies_to_niches) under the new name.

Deploy-skew is safe in both directions because ``is_validator_enabled`` fails
OPEN on a missing row: new code looking up ``company_claim`` before the rename
lands (or old code looking up ``glad_labs_claim`` after it) just runs the rule
at its default severity instead of silently dropping it.

The ``NOT EXISTS`` guard keeps the UPDATE from tripping the UNIQUE(name)
constraint in the (hand-edited-DB) corner where both names already exist.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE content_validator_rules
               SET name = 'company_claim'
             WHERE name = 'glad_labs_claim'
               AND NOT EXISTS (
                     SELECT 1 FROM content_validator_rules
                      WHERE name = 'company_claim'
                   )
            """
        )
    logger.info(
        "rename_glad_labs_claim_validator_rule_to_company_claim up: %s "
        "(UPDATE 0 = fresh install already seeded as company_claim)",
        status,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE content_validator_rules
               SET name = 'glad_labs_claim'
             WHERE name = 'company_claim'
               AND NOT EXISTS (
                     SELECT 1 FROM content_validator_rules
                      WHERE name = 'glad_labs_claim'
                   )
            """
        )
    logger.info(
        "rename_glad_labs_claim_validator_rule_to_company_claim down: %s",
        status,
    )
