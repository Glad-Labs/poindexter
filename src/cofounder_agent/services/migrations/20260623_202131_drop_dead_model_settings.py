"""Migration 20260623_202131: delete five orphaned model app_settings rows.

Retires five ``app_settings`` keys that are set in the seed but read by **no**
production code path anywhere in the tree (verified by full-repo grep):

  * ``pipeline_research_model``   — "Model for research stage (stage 1)"
  * ``pipeline_refinement_model`` — "Model for content refinement (stage 5)"
  * ``pipeline_social_model``     — "Model for social media post generation"
  * ``model_role_factchecker``    — the last unread survivor of the
    ``model_role_*`` scheme (``model_role_image_decision`` is still read by
    ``services/image_decision_agent.py`` and stays).
  * ``default_model_tier``        — a global cost-tier knob that was never
    wired (every call site hardcodes its own tier literal). A global override
    was considered and declined in favour of the existing granular per-step
    ``*_model`` pins, so it's retired rather than repurposed.

**Why they're dead.** The first three name *stages* of the 6-stage chunked
StageRunner flow that was deleted 2026-05-16 (Lane C Stage 4); the
``canonical_blog`` graph_def cutover (#355) replaced those stages with atoms
that resolve their model via ``pipeline_writer_model`` /
``pipeline_critic_model`` → ``resolve_tier_model`` (the
``cost_tier.<tier>.model`` fallback). The per-stage knobs were orphaned but the
seed rows lingered. The social path resolves ``cost_tier.standard`` →
``social_poster_fallback_model`` (``services/social_poster.py``), never the
``pipeline_social_model`` row. ``model_role_factchecker`` has no reader at all.

**Why this migration survives the squash.** A baseline only ever
``INSERT ... ON CONFLICT DO NOTHING``, which no-ops on installs that already
have the rows — so a baseline that simply omits these keys would leave existing
installs (prod) carrying them while fresh installs lack them. This is the
convergence step:

  * Fresh installs — the seed no longer emits these four rows, so the DELETE
    matches nothing (no-op).
  * Existing installs (prod, seeded from the pre-removal baseline) — this
    performs the real delete.

Both converge to a settings table free of the four dead keys. The next squash
can fold this away once every install has run it.

stdlib-only so the migrations-smoke CI step applies it without a full app boot.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = (
    "pipeline_research_model",
    "pipeline_refinement_model",
    "pipeline_social_model",
    "model_role_factchecker",
    "default_model_tier",
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            list(_DEAD_KEYS),
        )
    logger.info(
        "drop_dead_model_settings up: removed orphaned model settings %s (%s)",
        ", ".join(_DEAD_KEYS),
        result,
    )


async def down(pool) -> None:
    # No-op: these keys are retired (vestiges of the deleted StageRunner stages
    # + the unread model_role_* scheme) with no reader to restore. Re-seeding
    # them would only reintroduce set-but-never-read config. Same one-way
    # posture as 20260622_200222_drop_pipeline_tasks_category.
    logger.info(
        "drop_dead_model_settings down: no-op (refusing to re-seed retired keys)"
    )
