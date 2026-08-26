"""Migration 20260826_040508_add_decode_and_prefill_durations_to_cost_logs: add decode and prefill durations to cost_logs

ISSUE: Glad-Labs/glad-labs-stack#3340 follow-up (operator ask 2026-08-26)

``cost_logs.duration_ms`` is wall-clock call time, so the Model Throughput
surfaces (stack#3340) can only show *effective* output tok/s — prompt
processing and in-call queueing count against the model. Ollama reports the
split (``eval_duration`` = decode ns, ``prompt_eval_duration`` = prefill ns)
but LiteLLM's transformations drop it; ``services/llm_providers/
ollama_timings.py`` now recovers it onto the response and the dispatcher
persists it here. Nullable by design: cloud providers (anthropic/gemini/
openai) and pre-migration rows have no decode split — a NULL means "not
reported", never 0 (feedback_no_dummy_data).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE cost_logs "
            "ADD COLUMN IF NOT EXISTS decode_duration_ms integer, "
            "ADD COLUMN IF NOT EXISTS prefill_duration_ms integer"
        )
    logger.info("Migration add_decode_and_prefill_durations_to_cost_logs: applied")


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE cost_logs "
            "DROP COLUMN IF EXISTS decode_duration_ms, "
            "DROP COLUMN IF EXISTS prefill_duration_ms"
        )
    logger.info("Migration add_decode_and_prefill_durations_to_cost_logs: reverted")
