"""Migration: seed app_settings for the router outcome→weight feedback loop.

Glad-Labs/poindexter#361 part 1. Seeds the two operator-tunable knobs the
loop reads:

- ``router_feedback_alpha`` (0.2) — EWMA damping for the
  ``experiment_variants.weight`` nudge applied on each approve/reject in
  ``services/router_outcome_feedback.py``. Lower = more inertia.
- ``experiment_weighted_selection_enabled`` (false) — gates whether
  ``services/experiment_runner.py::pick_variant`` consumes those weights
  (weighted random) vs. the Phase-1 uniform allocation. Default OFF so prod
  variant picking stays uniform until an operator opts in; the weight column
  is maintained by the feedback loop regardless.

Idempotent — INSERT ... ON CONFLICT (key) DO NOTHING. ``value`` is never
NULL (app_settings.value is NOT NULL; '' is the unset sentinel, but these
have concrete defaults).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SETTINGS = [
    (
        "router_feedback_alpha",
        "0.2",
        "experiments",
        "EWMA damping for the outcome→experiment-variant-weight feedback "
        "loop (#361). new_weight = (1 - alpha) * old + alpha * signal, where "
        "signal=1.0 (approved) / 0.0 (rejected). Range (0, 1]; lower = more "
        "inertia. Default 0.2.",
    ),
    (
        "experiment_weighted_selection_enabled",
        "false",
        "experiments",
        "When true, experiment_runner.pick_variant allocates proportional to "
        "experiment_variants.weight (the column the #361 feedback loop nudges) "
        "instead of uniform random. Default false — prod variant picking stays "
        "uniform until opted in; the weight column is maintained either way.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _SETTINGS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
    logger.info(
        "Migration seed_router_feedback_settings: seeded %d app_settings",
        len(_SETTINGS),
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        keys = [k for k, *_ in _SETTINGS]
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])", keys,
        )
    logger.info("Migration seed_router_feedback_settings down: removed")
