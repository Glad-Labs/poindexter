"""Migration: seed the draft_gate approval gate DISABLED + re-seed canonical_blog graph_def.

Glad-Labs/poindexter#363 — convert approval gates to true LangGraph
``interrupt()`` pauses with checkpoint resume.

Two changes, both prod-safe (the gate is OFF by default so canonical_blog
behaviour is unchanged until an operator opts in):

1. Seed ``app_settings.pipeline_gate_draft_gate = 'off'`` — the enable flag
   the ``atoms.approval_gate`` atom checks via
   ``approval_service.is_gate_enabled('draft_gate', site_config)``. Default
   OFF means the new ``draft_gate`` node in canonical_blog is a pure
   pass-through no-op until the operator runs
   ``poindexter gates set draft_gate on``.

2. Re-seed the canonical_blog ``graph_def`` from
   ``services.canonical_blog_spec.CANONICAL_BLOG_GRAPH_DEF`` (which now
   includes the ``draft_gate`` node between ``generate_content`` and
   ``writer_self_review``). Because the gate is disabled, the only runtime
   effect is one extra no-op node per run until opt-in.

Light env (per CLAUDE.md migrations rules): imports ONLY stdlib + the pure
spec dict — no langgraph / langchain / template_runner, so migrations-smoke +
grafana-panels-lint apply it without the heavy pipeline deps.

Idempotent: ON CONFLICT DO NOTHING for the setting (preserves an operator's
on/off choice on re-run); the graph_def UPDATE is naturally idempotent.
"""

from __future__ import annotations

import json
import logging

from services.canonical_blog_spec import CANONICAL_BLOG_GRAPH_DEF

logger = logging.getLogger(__name__)

_SETTING_KEY = "pipeline_gate_draft_gate"
_SETTING_VALUE = "off"
_SETTING_DESC = (
    "HITL approval gate 'draft_gate': on/off. When on, the canonical_blog "
    "pipeline pauses after the writer stage via LangGraph interrupt() for "
    "operator approval (resume with `poindexter pipeline resume <task_id>`). "
    "Default off — prod runs are unaffected until opt-in (#363)."
)


async def up(pool) -> None:
    graph_json = json.dumps(CANONICAL_BLOG_GRAPH_DEF)
    async with pool.acquire() as conn:
        # 1. Seed the gate-enable flag DISABLED. ON CONFLICT DO NOTHING so a
        # later operator `gates set draft_gate on` is preserved on re-run.
        await conn.execute(
            """
            INSERT INTO app_settings
              (key, value, category, description, is_secret, is_active)
            VALUES ($1, $2, 'pipeline', $3, false, true)
            ON CONFLICT (key) DO NOTHING
            """,
            _SETTING_KEY, _SETTING_VALUE, _SETTING_DESC,
        )

        # 2. Re-seed the canonical_blog graph_def (now with the draft_gate
        # node). UPDATE the active row in place; idempotent. Only runs if a
        # canonical_blog template row exists — on a fresh DB the row is seeded
        # by the baseline / graph_def seed migration first (lexical ordering
        # puts that 2026-06-02 file before this one).
        result = await conn.execute(
            """
            UPDATE pipeline_templates
               SET graph_def = $2::jsonb,
                   updated_at = NOW()
             WHERE slug = $1
               AND active = true
            """,
            "canonical_blog", graph_json,
        )
        logger.info(
            "Migration seed_draft_gate_disabled: seeded %s=off; "
            "graph_def UPDATE -> %s",
            _SETTING_KEY, result,
        )


async def down(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM app_settings WHERE key = $1", _SETTING_KEY,
        )
    logger.info(
        "Migration seed_draft_gate_disabled down: removed %s "
        "(graph_def left as-is — re-run the prior graph_def seed to revert)",
        _SETTING_KEY,
    )
