"""Enqueue an image_rebuild pipeline task for an awaiting_approval draft.

This module used to BE the rebuild: ``ImageRebuildService`` ran the image
atoms in-process inside a worker HTTP request, so ``poindexter tasks
rebuild-images`` blocked the CLI (and a worker request slot) for the whole
render. The rebuild now runs as the ``image_rebuild`` graph template
(``services/image_rebuild_spec.py``) on the Prefect worker — same atoms,
queued — and this module shrank to the service-layer enqueue the route
delegates to (the CLI/HTTP adapters stay logic-free per the
transport-adapter contract).

Flow: validate the target is an awaiting_approval draft → insert a
``pipeline_tasks`` row (``template_slug='image_rebuild'``, metadata carrying
``target_task_id`` + ``allow_stock``) → return the new task id immediately.
The graph's entry atom re-validates the target's status at claim time, so an
approve/reject that lands in the queue gap fails the rebuild loud instead of
clobbering a decided draft.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_STATUS_SQL = "SELECT status FROM pipeline_tasks WHERE task_id = $1"
_TOPIC_SQL = "SELECT topic FROM pipeline_tasks WHERE task_id = $1"


async def enqueue_image_rebuild(
    pool: Any, task_id: str, *, allow_stock: bool = False
) -> str:
    """Queue an image_rebuild run for draft ``task_id``; return the new task id.

    Raises ValueError when the target is not an awaiting_approval draft —
    the same contract the synchronous path enforced.
    """
    status = await pool.fetchval(_STATUS_SQL, str(task_id))
    if status is None:
        raise ValueError(f"no pipeline_tasks row for task {task_id}")
    if status != "awaiting_approval":
        raise ValueError(
            f"rebuild-images requires an awaiting_approval draft; "
            f"task {task_id} is {status!r}"
        )
    topic = await pool.fetchval(_TOPIC_SQL, str(task_id)) or ""

    from services.tasks_db import TasksDatabase

    rebuild_task_id = await TasksDatabase(pool).add_task(
        {
            "task_type": "image_rebuild",
            "template_slug": "image_rebuild",
            "topic": topic,
            "status": "pending",
            "task_metadata": {
                "target_task_id": str(task_id),
                "allow_stock": bool(allow_stock),
            },
        }
    )
    logger.info(
        "[image_rebuild] queued rebuild task %s for draft %s (allow_stock=%s)",
        rebuild_task_id, task_id, allow_stock,
    )
    return rebuild_task_id


__all__ = ["enqueue_image_rebuild"]
