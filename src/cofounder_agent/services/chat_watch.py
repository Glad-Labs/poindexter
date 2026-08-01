"""Slim watched-run progress read for the Cofounder rail (poindexter#949).

The console polls this every ~5s while a conversation-linked pipeline task
is live, so it must stay cheap: task status + per-node spine from
``atom_runs`` (id/status/latency only — no output previews, no corpus) +
the expected node count from the task's ``graph_def``. The full deep-dive
stays on ``GET /api/trace/{task_id}`` (trace_read.get_trace).

``atom_runs`` capture is gated by ``atom_runs_capture_enabled``; with it
off the spine is honestly empty and the rail falls back to status-only —
never fabricated progress (feedback_no_dummy_data).
"""

from __future__ import annotations

from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)

TERMINAL_STATUSES = frozenset(
    {"published", "approved", "awaiting_approval", "rejected", "rejected_final",
     "dismissed", "failed", "cancelled", "completed"}
)


async def watch_task(pool: Any, task_id: str) -> dict[str, Any] | None:
    """Progress snapshot for one task; None when the task doesn't exist."""
    # quality_score comes from the latest pipeline_versions row —
    # pipeline_tasks has no such column (see the 2026-08-01 watcher-job
    # errata in services/jobs/chat_task_watch.py).
    task = await pool.fetchrow(
        """
        SELECT t.task_id, t.status, t.topic, t.template_slug, t.updated_at,
               (SELECT v.quality_score FROM pipeline_versions v
                 WHERE v.task_id = t.task_id
                 ORDER BY v.version DESC LIMIT 1) AS quality_score
          FROM pipeline_tasks t
         WHERE t.task_id = $1 OR t.id::text = $1
        """,
        task_id,
    )
    if task is None:
        return None

    expected_nodes = None
    slug = task["template_slug"]
    if slug:
        expected_nodes = await pool.fetchval(
            """
            SELECT jsonb_array_length(graph_def->'nodes')
              FROM pipeline_templates
             WHERE slug = $1 AND graph_def IS NOT NULL
            """,
            slug,
        )

    rows = await pool.fetch(
        """
        SELECT node_id, atom, status, duration_ms, started_at
          FROM atom_runs
         WHERE run_id = $1
         ORDER BY started_at
        """,
        task["task_id"],
    )
    nodes = [
        {
            "node_id": r["node_id"],
            "atom": r["atom"],
            "status": r["status"],
            "duration_ms": r["duration_ms"],
        }
        for r in rows
    ]
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "topic": task["topic"],
        "terminal": task["status"] in TERMINAL_STATUSES,
        "quality_score": (
            float(task["quality_score"]) if task["quality_score"] is not None else None
        ),
        "updated_at": (
            task["updated_at"].isoformat() if task["updated_at"] else None
        ),
        "expected_nodes": expected_nodes,
        "nodes_done": len(nodes),
        "nodes": nodes[-10:],
    }


__all__ = ["TERMINAL_STATUSES", "watch_task"]
