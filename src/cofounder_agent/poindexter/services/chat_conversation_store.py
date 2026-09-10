"""Conversation store for the Cofounder chat surface (poindexter#947).

CRUD over ``chat_conversations`` / ``chat_messages`` / ``chat_task_links``
plus the two lifecycle invariants the console leans on:

- **Persisted turn status.** Assistant turns are inserted ``streaming`` and
  finalized to ``complete`` / ``failed`` / ``interrupted``; the console
  renders exactly what the row says.
- **Lazy interrupted repair.** A worker restart mid-turn strands rows in
  ``pending``/``streaming``. :func:`repair_stale_turns` (called on every
  conversation read) flips rows older than the stale threshold to
  ``interrupted`` — no sweeper job needed for a low-volume table; reads
  self-heal (feedback_self_heal_not_suppress).

This module is the single owner of SQL against the chat tables — routes and
the agent loop go through it (transport-adapter contract).
"""

from __future__ import annotations

import json
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("id", "conversation_id"):
        if key in d and d[key] is not None:
            d[key] = str(d[key])
    for key in ("created_at", "last_message_at"):
        if key in d and d[key] is not None:
            d[key] = d[key].isoformat()
    if isinstance(d.get("parts"), str):
        try:
            d["parts"] = json.loads(d["parts"])
        except (TypeError, ValueError):
            d["parts"] = []
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (TypeError, ValueError):
            d["metadata"] = {}
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    return d


async def create_conversation(
    pool: Any,
    *,
    title: str = "",
    brain: str = "local",
    transport: str = "console",
    user_id: str = "operator",
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO chat_conversations (title, brain, transport, user_id)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        title[:200], brain, transport, user_id,
    )
    return _row_to_dict(row)


async def get_conversation(pool: Any, conversation_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM chat_conversations WHERE id = $1::uuid", conversation_id,
    )
    return _row_to_dict(row) if row else None


_LIST_SELECT = """
    SELECT c.*,
           (SELECT count(*) FROM chat_messages m
             WHERE m.conversation_id = c.id) AS message_count
      FROM chat_conversations c
"""


async def list_conversations(
    pool: Any, *, status: str = "active", limit: int = 50, user_id: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    if user_id:
        rows = await pool.fetch(
            _LIST_SELECT
            + " WHERE status = $1 AND user_id = $2"
            + " ORDER BY last_message_at DESC LIMIT $3",
            status, user_id, limit,
        )
    else:
        rows = await pool.fetch(
            _LIST_SELECT
            + " WHERE status = $1 ORDER BY last_message_at DESC LIMIT $2",
            status, limit,
        )
    return [_row_to_dict(r) for r in rows]


async def archive_conversation(pool: Any, conversation_id: str) -> bool:
    tag = await pool.execute(
        "UPDATE chat_conversations SET status = 'archived' WHERE id = $1::uuid",
        conversation_id,
    )
    return tag.endswith("1")


async def set_title_if_empty(pool: Any, conversation_id: str, title: str) -> None:
    await pool.execute(
        """
        UPDATE chat_conversations SET title = $2
         WHERE id = $1::uuid AND title = ''
        """,
        conversation_id, title[:200],
    )


async def repair_stale_turns(
    pool: Any, conversation_id: str | None, *, stale_after_seconds: int,
) -> int:
    """Flip stranded ``pending``/``streaming`` turns to ``interrupted``.

    ``conversation_id=None`` repairs across all conversations (used by the
    list view). Returns the number of repaired rows; a nonzero count is
    logged loudly because it means a turn died without finalizing.
    """
    if conversation_id:
        tag = await pool.execute(
            """
            UPDATE chat_messages
               SET turn_status = 'interrupted'
             WHERE turn_status IN ('pending', 'streaming')
               AND created_at < now() - make_interval(secs => $1)
               AND conversation_id = $2::uuid
            """,
            stale_after_seconds, conversation_id,
        )
    else:
        tag = await pool.execute(
            """
            UPDATE chat_messages
               SET turn_status = 'interrupted'
             WHERE turn_status IN ('pending', 'streaming')
               AND created_at < now() - make_interval(secs => $1)
            """,
            stale_after_seconds,
        )
    try:
        repaired = int(tag.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        repaired = 0
    if repaired:
        logger.error(
            "[chat] repaired %d stranded turn(s) to 'interrupted' — a worker "
            "restart or crash cut a turn short (conversation=%s)",
            repaired, conversation_id or "*",
        )
    return repaired


async def add_message(
    pool: Any,
    conversation_id: str,
    *,
    role: str,
    parts: list[dict[str, Any]] | None = None,
    turn_status: str = "complete",
    model: str = "",
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO chat_messages
                    (conversation_id, role, parts, turn_status, model)
                VALUES ($1::uuid, $2, $3::jsonb, $4, $5)
                RETURNING *
                """,
                conversation_id, role, json.dumps(parts or []), turn_status, model,
            )
            await conn.execute(
                "UPDATE chat_conversations SET last_message_at = now() "
                "WHERE id = $1::uuid",
                conversation_id,
            )
    return _row_to_dict(row)


async def finalize_message(
    pool: Any,
    message_id: str,
    *,
    parts: list[dict[str, Any]],
    turn_status: str,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
) -> None:
    await pool.execute(
        """
        UPDATE chat_messages
           SET parts = $2::jsonb,
               turn_status = $3,
               model = $4,
               prompt_tokens = $5,
               completion_tokens = $6,
               cost_usd = $7
         WHERE id = $1::uuid
        """,
        message_id, json.dumps(parts), turn_status, model,
        int(prompt_tokens), int(completion_tokens), float(cost_usd),
    )


async def list_messages(
    pool: Any, conversation_id: str, *, limit: int = 200,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    rows = await pool.fetch(
        """
        SELECT * FROM chat_messages
         WHERE conversation_id = $1::uuid
         ORDER BY created_at DESC
         LIMIT $2
        """,
        conversation_id, limit,
    )
    return [_row_to_dict(r) for r in reversed(rows)]


async def add_task_link(
    pool: Any, conversation_id: str, task_id: str, *, purpose: str = "created",
) -> None:
    await pool.execute(
        """
        INSERT INTO chat_task_links (conversation_id, pipeline_task_id, purpose)
        VALUES ($1::uuid, $2, $3)
        ON CONFLICT DO NOTHING
        """,
        conversation_id, task_id, purpose,
    )


async def list_task_links(pool: Any, conversation_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT pipeline_task_id, purpose, created_at
          FROM chat_task_links
         WHERE conversation_id = $1::uuid
         ORDER BY created_at
        """,
        conversation_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def tokens_used_today(pool: Any, *, user_id: str = "operator") -> int:
    """Chat tokens consumed today (UTC day) by this user's conversations.

    UTC-day on purpose: this feeds a coarse daily budget guard, not an
    operator-facing report — the ~operator_timezone offset error at the day
    boundary is acceptable for a rate limit and keeps the query index-friendly.
    """
    value = await pool.fetchval(
        """
        SELECT COALESCE(SUM(m.prompt_tokens + m.completion_tokens), 0)
          FROM chat_messages m
          JOIN chat_conversations c ON c.id = m.conversation_id
         WHERE c.user_id = $1
           AND m.created_at >=
               (date_trunc('day', now() AT TIME ZONE 'utc') AT TIME ZONE 'utc')
        """,
        user_id,
    )
    return int(value or 0)


__all__ = [
    "add_message",
    "add_task_link",
    "archive_conversation",
    "create_conversation",
    "finalize_message",
    "get_conversation",
    "list_conversations",
    "list_messages",
    "list_task_links",
    "repair_stale_turns",
    "set_title_if_empty",
    "tokens_used_today",
]
