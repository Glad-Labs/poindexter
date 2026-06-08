"""Migration: seed pipeline-streaming settings (#361 part 2).

Glad-Labs/poindexter#361 part 2 — stream pipeline progress to the operator
via an ``on_event`` callback so multi-minute runs aren't silent, with an
opt-in Telegram edit-streaming channel.

Seeds two ``app_settings`` keys (category ``pipeline``):

1. ``pipeline_streaming_channel`` default ``discord`` — routes the
   per-node ``on_event`` progress stream. Values:
   - ``discord``  (default): keep the existing Discord behaviour
     (per-node progress already fans out via ``_emit_progress`` →
     ``notify_operator(critical=False)``). The ``on_event`` default
     callback is a no-op so we don't double-post.
   - ``telegram``: opt-in edit-streaming — a SINGLE Telegram message is
     updated in place (``editMessageText``) with a running checklist.
     Telegram is the operator's critical-only channel, so this is
     deliberately opt-in (default keeps it off Telegram).
   - ``off``: ``on_event`` is a no-op (Discord ``_emit_progress`` still
     fires per its own ``template_runner_progress_streaming`` flag).

2. ``pipeline_streaming_min_edit_interval_s`` default ``5`` — throttle
   (seconds) between Telegram ``editMessageText`` calls so rapid node
   completions coalesce into one edit (Telegram rate-limits edits).

Light env (per CLAUDE.md migrations rules): stdlib + asyncpg pool only —
no langgraph / langchain / template_runner imports, so migrations-smoke +
grafana-panels-lint apply it without the heavy pipeline deps.

Idempotent: ON CONFLICT DO NOTHING preserves an operator's choice on
re-run. ``app_settings.value`` is NOT NULL — both defaults are non-empty
string literals (never NULL).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHANNEL_KEY = "pipeline_streaming_channel"
_CHANNEL_VALUE = "discord"
_CHANNEL_DESC = (
    "Where TemplateRunner.run streams per-node progress via its on_event "
    "callback: 'discord' (default — existing Discord progress feed; on_event "
    "is a no-op so we don't double-post), 'telegram' (opt-in: a single "
    "Telegram message edited in place with a running checklist — Telegram is "
    "the critical-only channel so this is off by default), or 'off' (on_event "
    "no-op; Discord _emit_progress still fires per its own flag). #361"
)

_INTERVAL_KEY = "pipeline_streaming_min_edit_interval_s"
_INTERVAL_VALUE = "5"
_INTERVAL_DESC = (
    "Minimum seconds between Telegram editMessageText calls when "
    "pipeline_streaming_channel='telegram'. Rapid node completions coalesce "
    "into one edit so we stay under Telegram's edit rate limit. #361"
)


async def up(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'pipeline', $3, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _CHANNEL_KEY, _CHANNEL_VALUE, _CHANNEL_DESC,
        )
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'pipeline', $3, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _INTERVAL_KEY, _INTERVAL_VALUE, _INTERVAL_DESC,
        )
    logger.info(
        "Migration seed_pipeline_streaming_settings: seeded %s=%s, %s=%s",
        _CHANNEL_KEY, _CHANNEL_VALUE, _INTERVAL_KEY, _INTERVAL_VALUE,
    )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = ANY($1::text[])",
            [_CHANNEL_KEY, _INTERVAL_KEY],
        )
    logger.info(
        "Migration seed_pipeline_streaming_settings down: removed %s, %s",
        _CHANNEL_KEY, _INTERVAL_KEY,
    )
