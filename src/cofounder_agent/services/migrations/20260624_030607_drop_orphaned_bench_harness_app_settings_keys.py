"""Migration 20260624_030607: delete the two orphaned bench harness app_settings rows.

``bench_default_prompt_count`` and ``bench_prometheus_url`` were seeded to
support ``scripts/bench/eval_cost_tiers.py`` — the CLI harness that benchmarked
the now-removed ``resolve_tier_model()`` cost-tier → model ladder.
``eval_cost_tiers.py`` was deleted in PR #1912 (docs + scripts follow-up to the
#1907 ``cost_tier.*`` removal), leaving these two settings with no code reader.

**Why this migration survives the squash.** A baseline only ever
``INSERT ... ON CONFLICT DO NOTHING``, so a baseline that simply omits these
rows leaves existing installs (prod, seeded before the removal) still carrying
them while fresh installs lack them. This is the convergence step:

  * Fresh installs — the seed no longer emits the two rows, so the DELETE
    matches nothing (no-op).
  * Existing installs (prod) — this performs the real delete.

Both converge to a settings table free of the orphaned bench rows. Same
one-way posture as 20260622_200222_drop_pipeline_tasks_category,
20260623_202131_drop_dead_model_settings, and
20260623_210500_drop_cost_tier_model_seeds.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    "bench_default_prompt_count",
    "bench_prometheus_url",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info(
        "drop_orphaned_bench_harness_app_settings_keys up: removed %s (%s)",
        ", ".join(_DEAD_KEYS),
        result,
    )


async def down(_pool) -> None:
    # No-op: eval_cost_tiers.py is deleted and has no replacement reader.
    # Re-inserting these rows would only restore dead configuration.
    logger.info(
        "drop_orphaned_bench_harness_app_settings_keys down: no-op "
        "(refusing to re-seed retired bench harness rows)"
    )
