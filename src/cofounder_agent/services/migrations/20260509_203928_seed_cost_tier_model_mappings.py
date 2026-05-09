"""Seed the four cost_tier.<tier>.model app_settings keys.

These bridge the ``cost_tier="standard"`` API that Lane B sweep agents
migrate call sites to (Glad-Labs/poindexter#450) and the concrete model
identifiers each provider consumes. Without these rows, the
``resolve_tier_model`` helper in
``services/llm_providers/dispatcher.py`` would raise on every call —
intentional, per ``feedback_no_silent_defaults.md``: a missing tier
mapping is a configuration bug, not a quiet fallback.

Defaults match what the Lane B inventory recommended after auditing
the 22 bucket-A occurrences plus the existing per-call-site fallback
keys (``pipeline_writer_model``, ``qa_fallback_critic_model``, etc.).
The mappings are deliberately overlapping with those existing keys —
this migration moves the *cost-tier* selection layer above them so
operators can swap a tier across all consumers in one row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_SEEDS = (
    (
        "cost_tier.free.model",
        "ollama/qwen3:8b",
        "Model resolved when callers pass cost_tier=free. Smallest local; image-decision tier.",
    ),
    (
        "cost_tier.budget.model",
        "ollama/gemma3:27b-it-qat",
        "Model resolved when callers pass cost_tier=budget. Quantized 27B; offline retention work.",
    ),
    (
        "cost_tier.standard.model",
        "ollama/gemma3:27b",
        "Model resolved when callers pass cost_tier=standard. Default writer + critic.",
    ),
    (
        "cost_tier.premium.model",
        "anthropic/claude-haiku-4-5",
        "Model resolved when callers pass cost_tier=premium. Cloud cross-model QA; cost_guard-gated.",
    ),
)


async def run_migration(conn) -> None:
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
        "Migration 20260509_203928_seed_cost_tier_model_mappings: applied "
        "(4 cost_tier.<tier>.model rows seeded if missing)"
    )
