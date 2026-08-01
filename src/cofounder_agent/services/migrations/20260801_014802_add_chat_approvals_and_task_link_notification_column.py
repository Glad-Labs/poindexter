"""Migration 20260801_014802: chat_approvals table + task-link notification stamp.

ISSUE: Glad-Labs/poindexter#949 (Cofounder P3)

Two pieces of the act+watch phase:

- ``chat_approvals`` — one row per write-tool approval card. When the chat
  agent's LLM requests a gated write tool, the turn does NOT block on a
  human: it inserts a ``pending`` row, renders an approval card, and
  completes. The operator's Approve click resolves the row atomically
  (``UPDATE … WHERE status='pending'`` — the one-shot guarantee) and only
  then executes the stored call. Deliberately NOT the existing
  ``approval_queue`` table: that is the MCP ``set_setting`` queue with no
  conversation/message linkage, no stored executable args, and no execution
  outcome — overloading it would couple two different lifecycles.
- ``chat_task_links.completed_notified_at`` — stamp for the watcher job
  (ChatTaskWatchJob): a linked pipeline task reaching a terminal status gets
  ONE completion message appended to its conversation + one operator ping,
  then the stamp prevents re-notification on every sweep.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def up(pool) -> None:
    """Apply the migration."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_approvals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                conversation_id UUID NOT NULL
                    REFERENCES chat_conversations(id) ON DELETE CASCADE,
                message_id UUID NOT NULL,
                tool TEXT NOT NULL,
                args JSONB NOT NULL DEFAULT '{}'::jsonb,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                resolved_at TIMESTAMPTZ,
                executed_ok BOOLEAN,
                result_digest TEXT NOT NULL DEFAULT '',
                CONSTRAINT chat_approvals_status_check
                    CHECK (status IN ('pending', 'approved', 'denied'))
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_approvals_pending
                ON chat_approvals (created_at)
                WHERE status = 'pending'
            """
        )
        await conn.execute(
            """
            ALTER TABLE chat_task_links
                ADD COLUMN IF NOT EXISTS completed_notified_at TIMESTAMPTZ
            """
        )
        # The watcher sweeps unnotified links; partial index keeps it cheap
        # as notified tombstones accumulate.
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_task_links_unnotified
                ON chat_task_links (created_at)
                WHERE completed_notified_at IS NULL
            """
        )
    logger.info("Migration add_chat_approvals: applied")


async def down(pool) -> None:
    """Revert the migration."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS chat_approvals")
        await conn.execute(
            "ALTER TABLE chat_task_links DROP COLUMN IF EXISTS completed_notified_at"
        )
    logger.info("Migration add_chat_approvals: reverted")
