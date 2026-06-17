"""``services.agent_permissions`` — permission gate for MCP write operations.

Thin layer over the ``agent_permissions`` + ``approval_queue`` tables used
by the MCP server's ``set_setting`` tool.  Extracted from
``mcp-server/server.py`` per the transport-adapter contract
(``docs/architecture/2026-06-10-transport-adapter-contract.md``,
epic #1340 / guard #1344).

The permission check fails CLOSED: if the ``agent_permissions`` row exists
and ``allowed=false``, :func:`check_write_permission` returns
``(False, requires_approval)``. The absence of a row is treated as
implicitly allowed (permissive default for an unconfigured gate).
"""

from __future__ import annotations

import json
from typing import Any

_SELECT_PERMISSION_SQL = (
    "SELECT allowed, requires_approval FROM agent_permissions "
    "WHERE agent_name = $1 AND resource = $2 AND action = $3"
)

_INSERT_APPROVAL_SQL = (
    "INSERT INTO approval_queue (agent_name, resource, action, proposed_change, reason) "
    "VALUES ($1, $2, $3, $4, $5)"
)


async def check_write_permission(
    pool: Any,
    agent_name: str,
    resource: str,
    action: str,
) -> tuple[bool, bool]:
    """Check whether ``agent_name`` is permitted to perform ``action`` on ``resource``.

    Returns ``(allowed, requires_approval)``.  When no row is found, the
    default is ``(True, False)`` — permissive for unconfigured agents.
    """
    row = await pool.fetchrow(_SELECT_PERMISSION_SQL, agent_name, resource, action)
    if row is None:
        return True, False
    return bool(row["allowed"]), bool(row["requires_approval"])


async def queue_for_approval(
    pool: Any,
    *,
    agent_name: str,
    resource: str,
    action: str,
    proposed_change: Any,
    reason: str,
) -> None:
    """Insert a pending approval request into the ``approval_queue`` table."""
    await pool.execute(
        _INSERT_APPROVAL_SQL,
        agent_name,
        resource,
        action,
        json.dumps(proposed_change),
        reason,
    )


__all__ = ["check_write_permission", "queue_for_approval"]
