"""Migration 20260618_003647_prune_dead_model_settings_keys_and_fix_qa_vision_model_ollama_prefix: prune dead model settings keys and fix qa vision model ollama prefix

Deletes 10 model-related app_settings keys that have zero callers in
production code (confirmed via codebase grep): model_role_writer,
model_role_critic, model_role_seo, model_role_summarizer,
model_role_image_prompt, image_generation_model, embedding_model,
pipeline_factcheck_model, pipeline_seo_model, local_llm_model_name.

Also fixes missing ``ollama/`` prefix on the two vision QA keys so that
LiteLLM routes them correctly: qa_vision_model and
qa_preview_vision_model are updated from ``qwen3-vl:30b`` to
``ollama/qwen3-vl:30b``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEAD_KEYS = [
    "model_role_writer",
    "model_role_critic",
    "model_role_seo",
    "model_role_summarizer",
    "model_role_image_prompt",
    "image_generation_model",
    "embedding_model",
    "pipeline_factcheck_model",
    "pipeline_seo_model",
    "local_llm_model_name",
]

_VISION_KEYS = ["qa_vision_model", "qa_preview_vision_model"]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            _DEAD_KEYS,
        )
        await conn.execute(
            """
            UPDATE app_settings
               SET value = 'ollama/' || value
             WHERE key = ANY($1::text[])
               AND value NOT LIKE 'ollama/%'
            """,
            _VISION_KEYS,
        )
    logger.info(
        "pruned %d dead model keys; fixed ollama/ prefix on %s",
        len(_DEAD_KEYS),
        _VISION_KEYS,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE app_settings
               SET value = substring(value from 8)
             WHERE key = ANY($1::text[])
               AND value LIKE 'ollama/%'
            """,
            _VISION_KEYS,
        )
    logger.info("rolled back ollama/ prefix fix on %s", _VISION_KEYS)
