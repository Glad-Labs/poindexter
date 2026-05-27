"""Migration 20260527_015444: rename cost_guard keys to match code + drop orphan daily_budget_usd

Why: ``services/cost_guard.py:316,317,636,637`` reads
``daily_spend_limit_usd`` and ``monthly_spend_limit_usd``. The
``0000_baseline.seeds.sql`` seed file (and Matt's live DB) shipped
the keys WITHOUT the ``_usd`` suffix. ``CostGuard._limit()`` falls
back to its second arg (2.0 / 100.0 USD) on read-miss, so operator-
tuned limits set via the UI were silently ignored — the actual cap
was the in-code default, not the DB value.

This rename also drops ``daily_budget_usd`` (orphan — no code reads
it, no tests reference it). It was a third never-wired alias added
2026-04 alongside the unsuffixed limits.

Detected by the 2026-05-27 operations audit (Lane C3 — cost guard).
Per ``feedback_no_silent_defaults``: the canonical key MUST be the
one the code reads. The legacy unsuffixed keys are renamed in place,
preserving any operator-tuned values; the orphan is dropped outright.

Idempotent: if the destination key already exists, the rename no-ops
(keeps the destination's value, drops the source). If the source
doesn't exist, the rename no-ops (already migrated). Re-running has
no effect on a converged DB.

Implementation note: asyncpg can't deduce the type of a placeholder
that appears in BOTH a ``regexp_replace`` (text) AND a key-column
context (varchar) — see ``feedback_asyncpg_type_cast_quirks``. The
rewrite uses two separate UPDATE statements and explicit ``::text``
casts at every parameter site.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_RENAMES = (
    (
        "daily_spend_limit",
        "daily_spend_limit_usd",
        "Maximum daily AI spend in USD (read by services/cost_guard.py)",
    ),
    (
        "monthly_spend_limit",
        "monthly_spend_limit_usd",
        "Maximum monthly AI spend in USD (read by services/cost_guard.py)",
    ),
)

_DROP_ORPHANS = ("daily_budget_usd",)


async def up(pool) -> None:
    """Rename unsuffixed cost-guard keys + drop the orphan budget key.

    DDL strategy:
      1. For each rename pair, copy the source value into the
         destination row if the destination doesn't exist, then
         delete the source row. Avoids the "duplicate key" path
         where both keys exist (destination wins, source dropped).
      2. Drop ``daily_budget_usd`` outright — no code reads it.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            for old_key, new_key, new_description in _RENAMES:
                await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_secret, is_active)
                    SELECT $2::varchar, value, category, $3::text,
                           is_secret, is_active
                      FROM app_settings
                     WHERE key = $1::varchar
                       AND NOT EXISTS (
                           SELECT 1 FROM app_settings WHERE key = $2::varchar
                       )
                    """,
                    old_key, new_key, new_description,
                )
                deleted = await conn.execute(
                    "DELETE FROM app_settings WHERE key = $1::varchar",
                    old_key,
                )
                if deleted.endswith(" 1"):
                    logger.info(
                        "Migration 20260527_015444: renamed %s -> %s",
                        old_key, new_key,
                    )

            for orphan_key in _DROP_ORPHANS:
                dropped = await conn.execute(
                    "DELETE FROM app_settings WHERE key = $1::varchar",
                    orphan_key,
                )
                if dropped.endswith(" 1"):
                    logger.info(
                        "Migration 20260527_015444: dropped orphan key %s "
                        "(no code reads this — superseded by daily_spend_limit_usd)",
                        orphan_key,
                    )


async def down(pool) -> None:
    """Recreate the unsuffixed legacy keys + the orphan default.

    Reverses ``up`` for rollback. The orphan ``daily_budget_usd``
    comes back as $5.00 (its original seed default) since we don't
    persist the previous value across the drop.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            for old_key, new_key, _ in _RENAMES:
                await conn.execute(
                    """
                    INSERT INTO app_settings
                        (key, value, category, description, is_secret, is_active)
                    SELECT $1::varchar, value, category, description,
                           is_secret, is_active
                      FROM app_settings
                     WHERE key = $2::varchar
                       AND NOT EXISTS (
                           SELECT 1 FROM app_settings WHERE key = $1::varchar
                       )
                    """,
                    old_key, new_key,
                )
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_secret, is_active)
                VALUES ('daily_budget_usd', '5.00', 'pipeline',
                        'Daily LLM API spend budget in USD', 'f', 't')
                ON CONFLICT (key) DO NOTHING
                """
            )
