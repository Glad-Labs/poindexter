"""Seed ``thinking_model_substrings`` for the ``is_thinking_model`` registry.

Pre-2026-05-09, four call sites each carried their own hardcoded list
of thinking-model substrings (``"qwen3"`` / ``"glm-4"`` /
``"deepseek-r1"``). The lists drifted: ``ai_content_generator``
included ``"deepseek-r1"`` while ``image_decision_agent`` did not;
``multi_model_qa`` was the only file matching ``"qwen3.5"`` and
``"qwen3:30b"``. Operators wanting to add a new thinking model had no
single seam — they had to grep the codebase.

This seed lands the canonical list as a JSON-encoded array in
``app_settings.thinking_model_substrings``. The
``services.llm_providers.thinking_models`` helper reads it; all four
call sites now route through the helper.

The default value is the union of the historical inline lists so
existing behavior is preserved on first deploy. Operators can edit
the row to add new thinking-model identifiers (e.g. when a new
``deepseek-r1.5`` family lands) without a code change.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_DEFAULT_VALUE = '["qwen3","qwen3.5","glm-4","glm-4.7","deepseek-r1"]'


async def run(conn) -> None:
    await conn.execute(
        """
        INSERT INTO app_settings (key, value, category, description, is_secret, is_active)
        VALUES (
            'thinking_model_substrings',
            $1,
            'llm_routing',
            'JSON array of substring needles used by services.llm_providers.thinking_models.is_thinking_model() to classify a model identifier as a thinking-model (one that produces <think>...</think> reasoning blocks). Add a needle to the array when a new thinking-model family lands; remove one to opt a family out of the higher max_tokens budget + /nothink prefix paths.',
            false,
            true
        )
        ON CONFLICT (key) DO NOTHING
        """,
        _DEFAULT_VALUE,
    )
    logger.info(
        "Migration 20260509_222554_seed_thinking_model_substrings: applied "
        "(thinking_model_substrings seeded if missing)"
    )
