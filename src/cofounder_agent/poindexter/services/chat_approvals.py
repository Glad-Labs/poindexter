"""Chat approval cards — queue, one-shot resolve, execute (poindexter#949).

The flow (spec: pause-free approval):

1. The agent loop hits a write tool with ``requires_approval`` → it calls
   :func:`create_approval` and tells the model the action is queued; the
   turn completes normally with a pending ``approval`` card part.
2. The operator clicks Approve/Deny → :func:`resolve_approval` flips the
   row **atomically** (``UPDATE … WHERE status='pending' RETURNING`` — a
   second click, another tab, or a replay loses the race and gets the
   already-resolved row back: the one-shot guarantee).
3. On approve it executes the stored call via the SAME registry handler the
   loop would have used, stamps the outcome onto the row AND the card part
   in ``chat_messages.parts``, and appends a ``system`` message so the
   thread (and the model's next-turn context) records what actually
   happened. Every resolution writes an ``audit_log``
   ``chat_approval_resolved`` row.

``agent_permissions`` (agent_name='console_chat', resource=<tool>,
action='execute') is the operator override lane, checked by the loop before
queueing: ``allowed=false`` forbids the tool outright; ``allowed=true,
requires_approval=false`` runs it inline without a card. No row = the
registry's declared default (card). An indeterminate check fails CLOSED to
the card — never to silent execution.
"""

from __future__ import annotations

import json
from typing import Any

from services import chat_conversation_store as store
from services.chat_tools import ChatToolContext, ChatToolError, get_tool
from services.logger_config import get_logger

logger = get_logger(__name__)

_RESULT_DIGEST_MAX = 600


async def create_approval(
    pool: Any,
    *,
    conversation_id: str,
    message_id: str,
    tool: str,
    args: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO chat_approvals
            (conversation_id, message_id, tool, args, summary)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5)
        RETURNING id, status, created_at
        """,
        conversation_id, message_id, tool, json.dumps(args), summary[:500],
    )
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
    }


async def get_approval(pool: Any, approval_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM chat_approvals WHERE id = $1::uuid", approval_id,
    )
    if row is None:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["conversation_id"] = str(d["conversation_id"])
    d["message_id"] = str(d["message_id"])
    if isinstance(d.get("args"), str):
        d["args"] = json.loads(d["args"])
    for k in ("created_at", "resolved_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def resolve_approval(
    *,
    pool: Any,
    db_service: Any,
    site_config: Any,
    approval_id: str,
    approve: bool,
    user_id: str = "operator",
) -> dict[str, Any]:
    """Atomically resolve a pending approval; execute on approve.

    Returns the resolved approval dict (with ``already_resolved: True``
    when this call lost the one-shot race). Raises ``KeyError`` for an
    unknown id.
    """
    new_status = "approved" if approve else "denied"
    row = await pool.fetchrow(
        """
        UPDATE chat_approvals
           SET status = $2, resolved_at = now()
         WHERE id = $1::uuid AND status = 'pending'
        RETURNING id, conversation_id, message_id, tool, args, summary
        """,
        approval_id, new_status,
    )
    if row is None:
        existing = await get_approval(pool, approval_id)
        if existing is None:
            raise KeyError(approval_id)
        existing["already_resolved"] = True
        return existing

    conversation_id = str(row["conversation_id"])
    message_id = str(row["message_id"])
    tool_name = row["tool"]
    args = row["args"]
    if isinstance(args, str):
        args = json.loads(args)

    executed_ok: bool | None = None
    result_digest = ""
    if approve:
        executed_ok, result_digest = await _execute(
            pool=pool, db_service=db_service, site_config=site_config,
            conversation_id=conversation_id, user_id=user_id,
            tool_name=tool_name, args=args,
        )
        await pool.execute(
            """
            UPDATE chat_approvals
               SET executed_ok = $2, result_digest = $3
             WHERE id = $1::uuid
            """,
            approval_id, executed_ok, result_digest,
        )

    await _stamp_card_part(
        pool, message_id=message_id, approval_id=approval_id,
        state=new_status, executed_ok=executed_ok, result_digest=result_digest,
    )

    # The thread records the outcome as a system message — visible to the
    # operator AND to the model's next-turn context tail.
    if approve:
        outcome = result_digest if result_digest else "(no output)"
        text = (
            f"Approved: {tool_name} — {'ok' if executed_ok else 'FAILED'}. "
            f"{outcome}"
        )
    else:
        text = f"Denied: {tool_name} — not executed."
    await store.add_message(
        pool, conversation_id, role="system",
        parts=[{"type": "markdown", "text": text}],
    )

    await _audit(
        pool, approval_id=approval_id, conversation_id=conversation_id,
        tool=tool_name, approved=approve, executed_ok=executed_ok,
    )

    resolved = await get_approval(pool, approval_id)
    assert resolved is not None
    return resolved


async def _execute(
    *,
    pool: Any,
    db_service: Any,
    site_config: Any,
    conversation_id: str,
    user_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[bool, str]:
    spec = get_tool(tool_name)
    if spec is None:
        return False, f"Tool {tool_name!r} no longer exists."
    ctx = ChatToolContext(
        db_service=db_service, site_config=site_config, pool=pool,
        user_id=user_id, conversation_id=conversation_id,
    )
    try:
        result = await spec.handler(ctx, **args)
        ok = True
    except ChatToolError as exc:
        result = str(exc)
        ok = False
    except TypeError as exc:
        result = f"Stored arguments no longer match the tool: {exc}"
        ok = False
    except Exception as exc:  # noqa: BLE001 — surfaced on the card + audited
        logger.exception("[chat_approvals] %s execution crashed", tool_name)
        result = f"{type(exc).__name__}: {exc}"
        ok = False
    # Approved side-effects may link tasks too (none of the P3 tools do,
    # but the seam stays uniform with the loop).
    for task_id in ctx.linked_task_ids:
        await store.add_task_link(pool, conversation_id, task_id)
    return ok, (result or "")[:_RESULT_DIGEST_MAX]


async def _stamp_card_part(
    pool: Any,
    *,
    message_id: str,
    approval_id: str,
    state: str,
    executed_ok: bool | None,
    result_digest: str,
) -> None:
    """Rewrite the approval card part inside chat_messages.parts.

    Read-modify-write on the JSONB: chat messages are only ever written by
    the turn that owns them or by this resolver, so the race window is nil
    in practice; the chat_approvals row stays the source of truth anyway.
    """
    row = await pool.fetchrow(
        "SELECT parts FROM chat_messages WHERE id = $1::uuid", message_id,
    )
    if row is None:
        logger.error(
            "[chat_approvals] message %s vanished — card not stamped", message_id,
        )
        return
    parts = row["parts"]
    if isinstance(parts, str):
        parts = json.loads(parts)
    changed = False
    for part in parts:
        card = part.get("card") if isinstance(part, dict) else None
        if (
            isinstance(card, dict)
            and card.get("kind") == "approval"
            and card.get("approval_id") == approval_id
        ):
            card["state"] = state
            card["executed_ok"] = executed_ok
            card["result_digest"] = result_digest
            changed = True
    if not changed:
        logger.error(
            "[chat_approvals] approval card %s not found on message %s",
            approval_id, message_id,
        )
        return
    await pool.execute(
        "UPDATE chat_messages SET parts = $2::jsonb WHERE id = $1::uuid",
        message_id, json.dumps(parts),
    )


async def _audit(
    pool: Any,
    *,
    approval_id: str,
    conversation_id: str,
    tool: str,
    approved: bool,
    executed_ok: bool | None,
) -> None:
    try:
        from services.audit_event_schemas import validate_event_details
        from services.audit_log import AuditLogger

        details = validate_event_details("chat_approval_resolved", {
            "schema_version": 1,
            "approval_id": approval_id,
            "conversation_id": conversation_id,
            "tool": tool,
            "approved": approved,
            "executed_ok": executed_ok,
        })
        await AuditLogger(pool).log(
            "chat_approval_resolved", "chat_approvals", details or {},
        )
    except Exception:  # noqa: BLE001 — telemetry must never break resolution
        logger.exception("[chat_approvals] audit write failed")


async def approval_policy(pool: Any, tool_name: str, *, default_card: bool) -> str:
    """Resolve a write tool's gate: 'forbid' | 'inline' | 'card'.

    Registry default (``default_card``) applies when no
    ``agent_permissions`` row exists for (console_chat, <tool>, execute).
    A row overrides entirely: allowed=false → forbid; allowed=true →
    requires_approval decides card vs inline. Any error checking → 'card'
    (fail closed to the human, never to silent execution — #750 spirit,
    inverted for a default-gated surface).
    """
    try:
        row = await pool.fetchrow(
            "SELECT allowed, requires_approval FROM agent_permissions "
            "WHERE agent_name = 'console_chat' AND resource = $1 "
            "AND action = 'execute'",
            tool_name,
        )
    except Exception:  # noqa: BLE001 — indeterminate check fails closed
        logger.exception(
            "[chat_approvals] agent_permissions check failed for %s", tool_name,
        )
        return "card"
    if row is None:
        return "card" if default_card else "inline"
    if not row["allowed"]:
        return "forbid"
    return "card" if row["requires_approval"] else "inline"


__all__ = [
    "approval_policy",
    "create_approval",
    "get_approval",
    "resolve_approval",
]
