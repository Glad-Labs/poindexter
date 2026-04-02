"""
Permission Service -- DB-driven access control for all system agents.

Every agent (LLM, service, API) has explicit permissions for each resource.
No implicit access. If it's not in the agent_permissions table, it's denied.

Permission levels:
- allowed=true, requires_approval=false: Direct access
- allowed=false, requires_approval=true: Must go through approval_queue
- allowed=false, requires_approval=false: Denied outright

Usage:
    from services.permission_service import check_permission, require_permission

    # Check before acting
    if await check_permission(pool, "openclaw", "app_settings", "write"):
        await update_setting(...)

    # Or propose a change if approval required
    result = await require_permission(pool, "openclaw", "prompt_templates", "write",
                                      proposed_change={"key": "blog.draft", "template": "..."})
    # result.status = "approved" | "queued_for_approval" | "denied"
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


@dataclass
class PermissionResult:
    allowed: bool
    requires_approval: bool
    status: str  # "allowed", "queued_for_approval", "denied"
    approval_id: Optional[int] = None
    reason: str = ""


async def check_permission(pool, agent_name: str, resource: str, action: str) -> bool:
    """Quick check: is this agent allowed to perform this action?"""
    row = await pool.fetchrow(
        "SELECT allowed, requires_approval FROM agent_permissions "
        "WHERE agent_name = $1 AND resource = $2 AND action = $3",
        agent_name, resource, action,
    )
    if row is None:
        logger.warning("[PERMS] No permission found for %s/%s/%s -- denied by default", agent_name, resource, action)
        return False
    return row["allowed"] and not row["requires_approval"]


async def require_permission(
    pool,
    agent_name: str,
    resource: str,
    action: str,
    proposed_change: Optional[dict] = None,
    reason: str = "",
) -> PermissionResult:
    """Check permission and handle approval flow if needed.

    Returns PermissionResult with status:
    - "allowed": Agent has direct access, proceed
    - "queued_for_approval": Change proposed, waiting for human
    - "denied": No access, not even with approval
    """
    row = await pool.fetchrow(
        "SELECT allowed, requires_approval FROM agent_permissions "
        "WHERE agent_name = $1 AND resource = $2 AND action = $3",
        agent_name, resource, action,
    )

    if row is None:
        logger.warning("[PERMS] No permission: %s/%s/%s", agent_name, resource, action)
        return PermissionResult(allowed=False, requires_approval=False, status="denied",
                                reason="No permission configured")

    if row["allowed"] and not row["requires_approval"]:
        return PermissionResult(allowed=True, requires_approval=False, status="allowed")

    if row["requires_approval"]:
        # Queue the proposed change for human review
        approval_id = None
        if proposed_change:
            result = await pool.fetchrow("""
                INSERT INTO approval_queue (agent_name, resource, action, proposed_change, reason)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
            """, agent_name, resource, action, json.dumps(proposed_change), reason[:500])
            approval_id = result["id"] if result else None
            logger.info("[PERMS] %s proposed %s on %s -- queued as approval #%s",
                        agent_name, action, resource, approval_id)

        return PermissionResult(
            allowed=False, requires_approval=True, status="queued_for_approval",
            approval_id=approval_id,
            reason=f"Requires human approval (queue #{approval_id})" if approval_id else "Requires approval",
        )

    return PermissionResult(allowed=False, requires_approval=False, status="denied",
                            reason="Action denied for this agent")


async def approve_change(pool, approval_id: int, reviewer: str = "matt") -> bool:
    """Approve a queued change and execute it."""
    row = await pool.fetchrow(
        "SELECT * FROM approval_queue WHERE id = $1 AND status = 'pending'",
        approval_id,
    )
    if not row:
        return False

    await pool.execute(
        "UPDATE approval_queue SET status = 'approved', reviewed_by = $1, reviewed_at = NOW() WHERE id = $2",
        reviewer, approval_id,
    )
    logger.info("[PERMS] Approval #%d approved by %s", approval_id, reviewer)
    return True


async def deny_change(pool, approval_id: int, reviewer: str = "matt") -> bool:
    """Deny a queued change."""
    await pool.execute(
        "UPDATE approval_queue SET status = 'denied', reviewed_by = $1, reviewed_at = NOW() WHERE id = $2",
        reviewer, approval_id,
    )
    return True


async def get_pending_approvals(pool) -> list:
    """Get all pending approval requests."""
    rows = await pool.fetch(
        "SELECT id, agent_name, resource, action, reason, created_at "
        "FROM approval_queue WHERE status = 'pending' ORDER BY created_at"
    )
    return [dict(r) for r in rows]
