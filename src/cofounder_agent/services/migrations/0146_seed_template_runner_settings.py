"""Migration 0146: seed app_settings keys for template_runner + architect.

Three new settings:

- ``template_runner_progress_to_telegram`` (bool, default false) —
  when on, every node start / completion / halt / failure inside
  TemplateRunner fans out to ``notify_operator()`` so the chat shows
  live pipeline progress. Off by default to keep the chat clean
  while the system is still tuning.
- ``pipeline_architect_model`` (string, default ``glm-4.7-5090:latest``) —
  which local model the architect-LLM uses for graph composition.
  Cloud models are opt-in here per the no-paid-APIs directive.
- ``pipeline_architect_timeout_seconds`` (float, default 120.0) — how
  long to wait for the architect LLM to emit its JSON spec.

Per the DB-first-config principle: every tunable lives in app_settings,
not as a hardcoded constant in code.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SEEDS: list[tuple[str, str, str, str]] = [
    # (key, value, category, description)
    (
        "template_runner_progress_to_telegram",
        "false",
        "pipeline",
        "When on, TemplateRunner emits per-node progress to operator "
        "Telegram chat via notify_operator(). Off by default — leaves "
        "the pipeline_events table as the audit trail without spamming.",
    ),
    (
        "pipeline_architect_model",
        "glm-4.7-5090:latest",
        "pipeline",
        "Local Ollama model the architect-LLM uses to compose pipelines "
        "from intent + atom catalog. Cloud models are opt-in only per "
        "the no-paid-APIs policy.",
    ),
    (
        "pipeline_architect_timeout_seconds",
        "120.0",
        "pipeline",
        "Max seconds to wait for the architect LLM to emit its JSON "
        "graph spec before timing out and falling back to a default "
        "template.",
    ),
]


async def up(pool) -> None:
    async with pool.acquire() as conn:
        for key, value, category, description in _SEEDS:
            await conn.execute(
                """
                INSERT INTO app_settings
                  (key, value, category, description, is_secret, is_active)
                VALUES ($1, $2, $3, $4, false, true)
                ON CONFLICT (key) DO NOTHING
                """,
                key, value, category, description,
            )
            logger.info("Migration 0146: seeded %s = %s", key, value)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        for key, _, _, _ in _SEEDS:
            await conn.execute(
                "DELETE FROM app_settings WHERE key = $1", key,
            )
        logger.info("Migration 0146 down: removed template_runner / architect seeds")
