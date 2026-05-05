"""Migration 0158: seed ``langfuse_tracing_enabled`` (default true).

Companion to migration 0153 (which seeded the three Langfuse credentials
used for prompt management). This row gives the operator a clean kill
switch for *tracing* without nuking *prompt management* — the two share
credentials but live on independent code paths.

Why a separate setting:

- Langfuse prompt management is read-mostly (one ``get_prompt`` call per
  pipeline stage) and the SDK fails closed → fallback to YAML.
- Langfuse tracing fires on every LLM call (success + failure) via
  LiteLLM's ``litellm.success_callback`` / ``litellm.failure_callback``
  hooks. If tracing is misbehaving (Langfuse container down, SDK bug,
  network blip causing latency), the operator wants to disable it
  without losing prompt overrides.

Default ``true`` — operators that have provisioned Langfuse credentials
almost always want tracing on by default. Per
``feedback_no_silent_defaults``: when this setting is ``true`` and any
of the three credential rows is empty, ``LiteLLMProvider`` raises at
worker startup so the operator knows the configuration is incomplete
(rather than silently shipping zero spans).

Issue: Glad-Labs/poindexter#373
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_KEY = "langfuse_tracing_enabled"
_VALUE = "true"
_CATEGORY = "observability"
_DESCRIPTION = (
    "When true (default), LiteLLMProvider registers Langfuse as a "
    "success/failure callback so every LLM call emits a span to the "
    "Langfuse host configured via langfuse_host + langfuse_public_key "
    "+ langfuse_secret_key (migration 0153). Set to false to disable "
    "tracing without affecting Langfuse prompt management — useful if "
    "the Langfuse stack is unhealthy and you don't want LLM calls "
    "paying the failed-callback latency cost. Note: the Langfuse "
    "Python SDK already batches + retries spans and never blocks the "
    "calling LLM request, so this kill switch is mostly defensive."
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, $3, $4, false, true)
            ON CONFLICT (key) DO UPDATE
              SET description = EXCLUDED.description,
                  category = EXCLUDED.category,
                  updated_at = NOW()
            """,
            _KEY, _VALUE, _CATEGORY, _DESCRIPTION,
        )
        logger.info("Migration 0158: seeded %s=%s", _KEY, _VALUE)


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM app_settings WHERE key = $1", _KEY)
        logger.info("Migration 0158 down: removed %s", _KEY)
