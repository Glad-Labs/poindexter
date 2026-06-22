"""``services.tasks_mcp`` — read helpers for the MCP server's task tools.

Transport-agnostic thin layer over ``pipeline_tasks_view`` for
``list_tasks`` and ``_resolve_task_id``.  Extracted from
``mcp-server/server.py`` per the transport-adapter contract
(``docs/architecture/2026-06-10-transport-adapter-contract.md``,
epic #1340 / guard #1344).
"""

from __future__ import annotations

from typing import Any

# Columns every list row carries. ``qa_feedback`` is the formatted rail
# breakdown (compile_meta → persist_task writes it to
# pipeline_tasks.qa_feedback) and ``qa_flagged`` is the self-heal-before-paging
# marker — a draft QA found non-approvable but did NOT discard. The flag lives
# in ``task_metadata`` JSON (no view rebuild), so derive it in SQL: a missing
# key or a non-flagged draft both read as false.
_LIST_COLS = (
    "task_id, topic, status, quality_score, created_at, qa_feedback, "
    "COALESCE((task_metadata->>'qa_flagged')::boolean, false) AS qa_flagged"
)

_LIST_TASKS_FILTERED_SQL = (
    f"SELECT {_LIST_COLS} "
    "FROM pipeline_tasks_view WHERE status = $1 ORDER BY created_at DESC LIMIT $2"
)

_LIST_TASKS_ALL_SQL = (
    f"SELECT {_LIST_COLS} "
    "FROM pipeline_tasks_view ORDER BY created_at DESC LIMIT $1"
)

_RESOLVE_PREFIX_SQL = (
    "SELECT task_id FROM pipeline_tasks_view WHERE task_id::text LIKE $1 || '%' LIMIT 1"
)

_TASK_QA_FEEDBACK_SQL = (
    "SELECT qa_feedback FROM pipeline_tasks_view WHERE task_id::text = $1 LIMIT 1"
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


async def get_task_qa_feedback(pool: Any, task_id: Any) -> str:
    """Return the formatted QA-rail feedback for a single task.

    This is the per-rail breakdown the operator reads to decide on a
    ``qa_flagged`` (or any awaiting_approval) post — score, vetoes, and
    advisory notes, as ``compile_meta`` formatted them. Returns ``""`` when
    the task has no row or no feedback recorded yet (never ``None``).
    """
    row = await pool.fetchrow(_TASK_QA_FEEDBACK_SQL, str(task_id))
    if row and row["qa_feedback"]:
        return str(row["qa_feedback"])
    return ""


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
