"""``services.tasks_mcp`` — read helpers for the MCP server's task tools.

Transport-agnostic thin layer over ``pipeline_tasks_view`` for
``list_tasks`` and ``_resolve_task_id``.  Extracted from
``mcp-server/server.py`` per the transport-adapter contract
(``docs/architecture/2026-06-10-transport-adapter-contract.md``,
epic #1340 / guard #1344).
"""

from __future__ import annotations

from typing import Any

_LIST_TASKS_FILTERED_SQL = (
    "SELECT task_id, topic, status, quality_score, created_at "
    "FROM pipeline_tasks_view WHERE status = $1 ORDER BY created_at DESC LIMIT $2"
)

_LIST_TASKS_ALL_SQL = (
    "SELECT task_id, topic, status, quality_score, created_at "
    "FROM pipeline_tasks_view ORDER BY created_at DESC LIMIT $1"
)

_RESOLVE_PREFIX_SQL = (
    "SELECT task_id FROM pipeline_tasks_view WHERE task_id::text LIKE $1 || '%' LIMIT 1"
)


async def list_tasks(
    pool: Any,
    *,
    status: str = "all",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return pipeline tasks ordered by creation time, newest first.

    ``status='all'`` returns all statuses. Any other value is used as an
    exact ``WHERE status = $1`` filter. ``limit`` is capped at 100.
    """
    limit = min(limit, 100)
    if status != "all":
        rows = await pool.fetch(_LIST_TASKS_FILTERED_SQL, status, limit)
    else:
        rows = await pool.fetch(_LIST_TASKS_ALL_SQL, limit)
    return [dict(r) for r in rows]


async def resolve_task_prefix(pool: Any, prefix: str) -> str:
    """Resolve a short task-ID prefix to the full UUID string.

    Returns the full UUID if a match is found; returns ``prefix``
    unchanged if no match or if ``prefix`` is already a full UUID
    (>= 32 chars, skips the DB round-trip).
    """
    if len(prefix) >= 32:
        return prefix
    row = await pool.fetchrow(_RESOLVE_PREFIX_SQL, prefix)
    if row:
        return str(row["task_id"])
    return prefix


__all__ = ["list_tasks", "resolve_task_prefix"]
