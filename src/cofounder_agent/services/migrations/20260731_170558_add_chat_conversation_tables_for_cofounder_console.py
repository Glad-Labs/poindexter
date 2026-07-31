"""Migration 20260731_170558: add chat conversation tables for the Cofounder console.

ISSUE: Glad-Labs/poindexter#947 (Cofounder P1)

The console's Cofounder chat surface persists conversations in Postgres (the
spinal cord) so turns survive worker restarts and the thread can render honest
turn state instead of a hang:

- ``chat_conversations`` — one row per conversation. ``transport`` exists from
  day one so the voice surface can migrate into this store later (P6:
  ``voice_messages`` -> ``transport='voice'``) and the cofounder is one
  identity across text + voice. ``user_id`` defaults to ``'operator'`` — the
  SaaS auth epic maps real users onto it later without a schema change.
- ``chat_messages`` — one row per message. ``parts`` is the typed-parts JSONB
  (markdown / tool_call / card) the console renders; ``turn_status`` is the
  persisted turn lifecycle (pending -> streaming -> complete | failed |
  interrupted) that makes a mid-turn worker restart render "interrupted —
  retry" instead of spinning forever.
- ``chat_task_links`` — conversation <-> pipeline_tasks watches ("run #N
  started from this thread"), read by the P3 activity-rail progress poll.

Retention ships in the same migration so the tables never exist unguarded:
archived conversations' messages TTL out after 90 days; links follow their
conversation via FK cascade. The policy is seeded ``enabled=false`` like the
other shipped policies — enabling is an operator decision.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id TEXT NOT NULL DEFAULT 'operator',
                title TEXT NOT NULL DEFAULT '',
                brain TEXT NOT NULL DEFAULT 'local',
                transport TEXT NOT NULL DEFAULT 'console',
                status TEXT NOT NULL DEFAULT 'active',
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT chat_conversations_status_check
                    CHECK (status IN ('active', 'archived')),
                CONSTRAINT chat_conversations_brain_check
                    CHECK (brain IN ('local', 'claude_code'))
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL
                    REFERENCES chat_conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                parts JSONB NOT NULL DEFAULT '[]'::jsonb,
                turn_status TEXT NOT NULL DEFAULT 'complete',
                model TEXT NOT NULL DEFAULT '',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT chat_messages_role_check
                    CHECK (role IN ('user', 'assistant', 'system')),
                CONSTRAINT chat_messages_turn_status_check
                    CHECK (turn_status IN
                        ('pending', 'streaming', 'complete', 'failed', 'interrupted'))
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
                ON chat_messages (conversation_id, created_at)
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_streaming
                ON chat_messages (created_at)
                WHERE turn_status IN ('pending', 'streaming')
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_task_links (
                conversation_id UUID NOT NULL
                    REFERENCES chat_conversations(id) ON DELETE CASCADE,
                pipeline_task_id TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT 'created',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (conversation_id, pipeline_task_id)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_conversations_recent
                ON chat_conversations (status, last_message_at DESC)
            """
        )
        await conn.execute(
            """
            INSERT INTO retention_policies
                (name, handler_name, table_name, filter_sql, age_column,
                 ttl_days, enabled, metadata)
            SELECT
                'chat_messages.archived', 'ttl_prune', 'chat_messages',
                'conversation_id IN (SELECT id FROM chat_conversations '
                    || 'WHERE status = ''archived'')',
                'created_at', 90, false,
                '{"issue": "poindexter#947"}'::jsonb
            WHERE NOT EXISTS (
                SELECT 1 FROM retention_policies
                WHERE name = 'chat_messages.archived'
            )
            """
        )
    logger.info("Migration add_chat_conversation_tables: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retention_policies WHERE name = 'chat_messages.archived'"
        )
        await conn.execute("DROP TABLE IF EXISTS chat_task_links")
        await conn.execute("DROP TABLE IF EXISTS chat_messages")
        await conn.execute("DROP TABLE IF EXISTS chat_conversations")
    logger.info("Migration add_chat_conversation_tables: reverted")
