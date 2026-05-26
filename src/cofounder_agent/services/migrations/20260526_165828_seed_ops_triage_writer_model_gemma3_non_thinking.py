"""Seed ``ops_triage_writer_model`` so the brain's triage LLM stops
silently consuming every token on ``<think>`` blocks.

Captured 2026-05-26: every brain triage LLM call (``/api/triage``)
was returning ``tokens=0`` and empty diagnosis prose because the
``_DefaultModelRouter`` in ``routes/triage_routes.py`` fell through to
``resolve_local_model()`` which on Matt's prod returns
``pipeline_writer_model`` — currently ``glm-4.7-5090``, a thinking-
mode model. With ``ops_triage_max_diagnosis_tokens=400`` capping the
budget and a thinking model burning ~400 tokens on its reasoning
trace before emitting any user-facing prose, every triage call
finished with ``response=''`` and ``done_reason=length``.

Symptom: brain alert prose said "Ollama is unresponsive to inference
requests" when Ollama was fine — the reasoner was just synthesizing
that conclusion from the empty triage output + downstream
``cost_freshness`` staleness signals.

Fix in PR alongside this migration:

  - ``services/llm_providers/thinking_models.py`` — new
    ``strip_think_blocks()`` canonical helper.
  - ``routes/triage_routes.py:_DefaultModelRouter`` — reads
    ``ops_triage_writer_model`` first, falls back to
    ``resolve_local_model()`` only when unset. Strips
    ``<think>...</think>`` blocks from every response regardless of
    which model resolved.

This migration seeds the new app_setting to ``gemma3:27b`` — verified
local-Ollama: returns real prose in ~13s, eval_count=44, no thinking
trace. Operators can swap to any other non-thinking model with
``poindexter set ops_triage_writer_model <name>``.

ON CONFLICT DO NOTHING — re-runnable, never overwrites operator-set
values.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, description, category, is_secret)
            VALUES (
              'ops_triage_writer_model',
              'gemma3:27b',
              'Local Ollama model used for brain alert triage (the /api/triage endpoint). '
              'Defaults to gemma3:27b because thinking-mode models (glm-4.7, qwen3 family) '
              'consume their entire token budget on <think> reasoning before emitting any '
              'user-facing prose, leaving the operator-facing diagnosis empty. Override only '
              'with non-thinking models, OR pair a thinking model with a much larger '
              'ops_triage_max_diagnosis_tokens (>2000) so the actual response fits past the '
              'thinking trace. Defensive <think> stripping is applied regardless of which '
              'model resolves, so a thinking model will not crash the path — but the prose '
              'quality is still better with a non-thinking model.',
              'ops-triage',
              false
            )
            ON CONFLICT (key) DO NOTHING;
            """
        )
        logger.info(
            "Migration seed_ops_triage_writer_model_gemma3_non_thinking: applied",
        )


async def down(pool) -> None:
    """Remove the seeded row. The triage path falls back to
    ``resolve_local_model()`` (which reads ``pipeline_writer_model``)
    when the setting is missing, so a missing row reproduces the
    pre-2026-05-26 behaviour."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM app_settings
            WHERE key = 'ops_triage_writer_model';
            """
        )
        logger.info(
            "Migration seed_ops_triage_writer_model_gemma3_non_thinking down: reverted",
        )
