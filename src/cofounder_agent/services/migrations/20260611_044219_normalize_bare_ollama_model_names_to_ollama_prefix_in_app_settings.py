"""Migration 20260611_044219_normalize_bare_ollama_model_names_to_ollama_prefix_in_app_settings

Some model settings were seeded with bare Ollama model names (e.g.
``gemma4:31b``) while others already used the explicit ``ollama/`` prefix.
LiteLLM accepts both — bare names route via the configured api_base — but the
inconsistency caused the cost_logs.model column to store both forms, making
"is this a local call?" checks ambiguous. This migration backfills the 9
affected keys to the canonical ``ollama/<name>`` form. Two keys are
intentionally left bare: ``preferred_ollama_model`` (matched against the raw
Ollama model-list API) and ``voice_agent_llm_model`` (sent directly to
Ollama's OpenAI-compat endpoint, which does not accept the prefix).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UPDATES = {
    "embedding_collapse_summary_model": "ollama/gemma-4-31B-it-qat:latest",
    "memory_compression_summary_model": "ollama/gemma-4-31B-it-qat:latest",
    "ops_triage_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "pipeline_architect_model": "ollama/glm-4.7-5090:latest",
    "podcast_script_model": "ollama/gemma4:31b",
    "qa_fallback_critic_model": "ollama/gemma4:31b",
    "qa_fallback_writer_model": "ollama/gemma-4-31B-it-qat:latest",
    "structured_extraction_model": "ollama/gemma-4-31B-it-qat:latest",
    "vision_alt_model": "ollama/qwen3-vl:30b",
}


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _UPDATES.items():
            bare = value.removeprefix("ollama/")
            await conn.execute(
                """
                UPDATE app_settings
                SET value = $1
                WHERE key = $2
                  AND value = $3
                """,
                value,
                key,
                bare,
            )
        logger.info(
            "normalize_bare_ollama_model_names: backfilled %d keys", len(_UPDATES)
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, value in _UPDATES.items():
            bare = value.removeprefix("ollama/")
            await conn.execute(
                """
                UPDATE app_settings
                SET value = $1
                WHERE key = $2
                  AND value = $3
                """,
                bare,
                key,
                value,
            )
        logger.info(
            "normalize_bare_ollama_model_names: reverted %d keys", len(_UPDATES)
        )
