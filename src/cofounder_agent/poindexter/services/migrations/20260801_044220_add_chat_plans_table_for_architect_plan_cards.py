"""Migration 20260801_044220: chat_plans table for architect plan cards.

ISSUE: Glad-Labs/poindexter#950 (Cofounder P4)

The chat agent's ``plan_pipeline`` tool composes an ad-hoc pipeline via
``pipeline_architect.compose`` and renders a plan card. This table is the
card's durable server side (the ``chat_approvals`` pattern): the composed
spec + its cached ``pipeline_templates`` slug + the topic the eventual task
should carry. ``Run pipeline`` resolves atomically (``status='draft'`` →
``'ran'`` — one-shot, double clicks lose the race) and creates the
``pipeline_tasks`` row with ``template_slug`` = the cached slug, which the
P3 watch machinery then follows. Adjust is conversational (a recompose
produces a new plan row), so no adjust state lives here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_plans (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL
                    REFERENCES chat_conversations(id) ON DELETE CASCADE,
                message_id UUID NOT NULL,
                intent TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                template_slug TEXT NOT NULL,
                spec JSONB NOT NULL DEFAULT '{}'::jsonb,
                status TEXT NOT NULL DEFAULT 'draft',
                task_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                resolved_at TIMESTAMPTZ,
                CONSTRAINT chat_plans_status_check
                    CHECK (status IN ('draft', 'ran'))
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_plans_draft
                ON chat_plans (created_at)
                WHERE status = 'draft'
            """
        )
    logger.info("Migration add_chat_plans_table: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS chat_plans")
    logger.info("Migration add_chat_plans_table: reverted")
