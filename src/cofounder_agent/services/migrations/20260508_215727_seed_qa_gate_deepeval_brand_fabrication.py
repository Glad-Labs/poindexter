"""Seed the ``deepeval_brand_fabrication`` qa_gates row + opt-in flag.

First production wire-in of DeepEval (sub-issue 1 of glad-labs-stack#329 epic).
Uses the ``BrandFabricationMetric`` already implemented in
``services/deepeval_rails.py`` — wraps the existing
``content_validator`` fabrication patterns as a DeepEval ``BaseMetric``.

The metric is duplicative with ``programmatic_validator`` by design at
this stage: the goal is to establish the deepeval call-path from
production code so the heavier follow-ups (G-Eval + FaithfulnessMetric
that feed the auto-curator) reuse the same plumbing — qa_gates row,
advisory mode, test fixtures, error swallowing.

Defaults:
- ``required_to_pass = False`` — advisory only, never blocks publish
- ``enabled = True`` — runs by default since deepeval is already
  importable in the worker env (verified 2026-05-08)
- ``execution_order = 700`` — after vision_gate (600), before any
  future deepeval gates (G-Eval at 710, Faithfulness at 720, etc.)

Also seeds ``app_settings.deepeval_enabled = "true"`` so the rail's
``is_enabled`` check passes without a manual flip.
"""

import json

from services.logger_config import get_logger

logger = get_logger(__name__)


_GATE_NAME = "deepeval_brand_fabrication"
_FLAG_KEY = "deepeval_enabled"


async def up(pool) -> None:
    async with pool.acquire() as conn:
        # 1. qa_gates row — same shape as 0094 seed.
        metadata = json.dumps({
            "description": (
                "DeepEval BrandFabricationMetric — wraps content_validator "
                "fabrication patterns as a DeepEval BaseMetric. Advisory."
            ),
            "epic": "glad-labs-stack#329",
            "scaffold": "services/deepeval_rails.py",
            "seeded_by_migration": "20260508_215727",
        })
        await conn.execute(
            """
            INSERT INTO qa_gates
                (name, stage_name, execution_order, reviewer,
                 required_to_pass, enabled, config, metadata)
            VALUES ($1, 'qa', 700, $1, FALSE, TRUE, '{}'::jsonb, $2::jsonb)
            ON CONFLICT (name) DO NOTHING
            """,
            _GATE_NAME, metadata,
        )

        # 2. app_settings flag — insert "true" explicitly so the rail
        # activates without a manual flip. NOT NULL on app_settings.value
        # is a hard rule (per feedback_app_settings_value_not_null), so we
        # never write NULL here. The original draft of this migration also
        # set value_type='bool', but app_settings has no value_type column
        # — type coercion is the consumer's job (settings_service casts
        # from string when callers ask for bool/int/json).
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description)
            VALUES ($1, 'true', $2)
            ON CONFLICT (key) DO NOTHING
            """,
            _FLAG_KEY,
            "Master switch for the DeepEval rails (services/deepeval_rails.py). "
            "When true, the deepeval qa_gates rows execute. When false, all "
            "deepeval reviewers no-op cleanly. Default true since deepeval "
            "ships with the worker.",
        )

        logger.info(
            "[migration] seeded qa_gates row %r + app_settings.%s",
            _GATE_NAME, _FLAG_KEY,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM qa_gates WHERE name = $1", _GATE_NAME,
        )
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1", _FLAG_KEY,
        )
        logger.info(
            "[migration] removed qa_gates row %r + app_settings.%s",
            _GATE_NAME, _FLAG_KEY,
        )
