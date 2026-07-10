"""Rebase the dev_diary job's DB schedule to operator-local semantics.

Convergence step for the tz-aware scheduler. `_seed_job_config_if_absent` wrote
`plugin.job.run_dev_diary_post` with `ON CONFLICT DO NOTHING` on first
registration, so existing installs hold `config.schedule = "0 13 * * *"` (the
old UTC-baked default). With the scheduler now evaluating crons in the operator
zone (services/clock.py), that would fire at 1pm local. This rewrites the row to
`"0 9 * * *"` ONLY while it still equals the old default, so a hand-tuned
schedule is never clobbered. Idempotent (re-run after a fix finds no match).
No-op on fresh installs (the seeder now writes 0 9 from the code default).

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
            SET value = jsonb_set(value::jsonb, '{config,schedule}', '"0 9 * * *"')::text
            WHERE key = 'plugin.job.run_dev_diary_post'
              AND value::jsonb #>> '{config,schedule}' = '0 13 * * *'
            """
        )
    logger.info(
        "rebase_dev_diary_cron_local up: rewrote dev_diary schedule 0 13 -> 0 9 "
        "where still at the old UTC default (no-op otherwise)"
    )


async def down(pool) -> None:
    # No-op: reversing to 0 13 would re-break the fire time under the tz-aware
    # scheduler. Same one-way posture as drop_pipeline_tasks_category.
    logger.info(
        "rebase_dev_diary_cron_local down: no-op (refusing to re-introduce the UTC-baked schedule)"
    )
