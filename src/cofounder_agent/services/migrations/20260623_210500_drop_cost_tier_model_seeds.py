"""Migration 20260623_210500: delete the four cost_tier.<tier>.model app_settings rows.

Retires the cost-tier → model indirection. Every pipeline step now reads its
own dedicated per-step ``*_model`` pin and fails loud when unset, so the
``cost_tier.{free,budget,standard,premium}.model`` mapping rows (and the
``resolve_tier_model`` helper that read them) have no remaining reader.

  * ``cost_tier.free.model``
  * ``cost_tier.budget.model``
  * ``cost_tier.standard.model``
  * ``cost_tier.premium.model``

(The ``flagship`` tier appeared in the resolver's name tuple but was never
seeded, so there is no row to drop for it.)

**Why the indirection was removed.** A single tier knob shared across many
unrelated steps meant tuning one step's model silently moved every other step
that resolved the same tier. The per-step pins
(``pipeline_writer_model``, ``pipeline_critic_model``, ``deepeval_judge_model``,
``ragas_judge_model``, ``podcast_script_model``, ``video_director_model`` /
``video_scene_model`` / ``video_slideshow_prompt_model``,
``embedding_collapse_summary_model``, ``memory_compression_summary_model``,
``image_search_query_model``, ``sdxl_prompt_model``, ``writer_self_review_model``,
…) give granular control and a loud failure when a step's model is missing — no
quiet fallthrough to a shared default.

**Why this migration survives the squash.** A baseline only ever
``INSERT ... ON CONFLICT DO NOTHING``, so a baseline that simply omits these
rows leaves existing installs (prod, seeded before the removal) still carrying
them while fresh installs lack them. This is the convergence step:

  * Fresh installs — the seed no longer emits the four rows, so the DELETE
    matches nothing (no-op).
  * Existing installs (prod) — this performs the real delete.

Both converge to a settings table free of the cost_tier model rows. The next
squash can fold this away once every install has run it. Same one-way posture
as 20260622_200222_drop_pipeline_tasks_category and
20260623_202131_drop_dead_model_settings.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    "cost_tier.free.model",
    "cost_tier.budget.model",
    "cost_tier.standard.model",
    "cost_tier.premium.model",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info(
        "drop_cost_tier_model_seeds up: removed cost-tier model rows %s (%s)",
        ", ".join(_DEAD_KEYS),
        result,
    )


async def down(pool) -> None:
    # No-op: the cost_tier indirection is retired with no reader to restore
    # (resolve_tier_model was deleted). Re-seeding would only reintroduce the
    # shared-tier config the per-step pins replaced. Same one-way posture as
    # 20260623_202131_drop_dead_model_settings.
    logger.info(
        "drop_cost_tier_model_seeds down: no-op (refusing to re-seed retired cost_tier rows)"
    )
