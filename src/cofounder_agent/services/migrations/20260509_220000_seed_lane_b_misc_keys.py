"""Seed per-call-site fallback keys for Lane B batch 2 sweep #4 (misc leaf utilities).

Three new ``app_settings`` keys land here as the no-silent-defaults
backstop for the call sites migrated in this sweep:

- ``social_poster_fallback_model`` — used by ``services.social_poster``
  when ``cost_tier='standard'`` resolution fails.
- ``video_slideshow_prompt_model`` — used by ``services.video_service``
  for the SDXL prompt-generation Ollama call. Deliberately
  non-thinking (the call returns empty on glm/qwen-thinking models);
  ``llama3:latest`` is the deliberate floor here.
- ``task_executor_first_retry_writer_model`` — used by
  ``services.task_executor`` to swap writers on the first retry.
  Intent-based, NOT a tier migration — the value is the writer the
  retry strategy intentionally swaps to, not a fallback.

Together with the existing ``cost_tier.<tier>.model`` rows seeded by
``20260509_203928_seed_cost_tier_model_mappings``, these complete the
no-silent-defaults guarantee for the Lane B batch 2 surface.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS = (
    (
        "social_poster_fallback_model",
        "ollama/llama3:latest",
        "Per-call-site backstop for services.social_poster when "
        "cost_tier='standard' resolution fails (Lane B batch 2 sweep).",
    ),
    (
        "video_slideshow_prompt_model",
        "ollama/llama3:latest",
        "Per-call-site backstop for services.video_service SDXL prompt-gen. "
        "Deliberately non-thinking — qwen3/glm-4 thinking variants return empty "
        "on this prompt shape (Lane B batch 2 sweep).",
    ),
    (
        "task_executor_first_retry_writer_model",
        "ollama/qwen3-coder:30b",
        "Writer model that services.task_executor swaps to on the first retry. "
        "Intent-based — picks a different model from the default writer to dodge "
        "model-specific failure modes (Lane B batch 2 sweep).",
    ),
)


async def run(conn) -> None:
    for key, value, description in _SEEDS:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'llm_routing', $3, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            key, value, description,
        )
    logger.info(
        "Migration 20260509_220000_seed_lane_b_misc_keys: applied "
        "(3 per-call-site fallback keys seeded if missing)"
    )
