"""``sweep_stale_tasks`` vs a heartbeat-refreshed row (poindexter#757 / GH-90).

``heartbeat_task`` was built to keep ``pipeline_tasks.updated_at`` fresh
during long-running stages so the stale-task sweep wouldn't reclaim a task
that's genuinely still working — but its only caller (``TaskExecutor``) was
deleted in the 2026-05-16 Prefect cutover, so the heartbeat went dark and
the sweep has judged every run purely by claim-time age ever since.
``services/template_runner.py::_ainvoke_with_content_heartbeat`` re-wires
it as a sidecar.

This test proves the two halves actually compose against the real schema:
a task whose ``updated_at`` is old by wall-clock but was JUST refreshed by
``heartbeat_task`` must survive ``sweep_stale_tasks``; one that never got a
heartbeat must still be reclaimed exactly as before (no regression).

Run with::

    poetry run pytest tests/integration_db/test_sweep_respects_pipeline_task_heartbeat.py \
        -m integration_db -q
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]

_COLUMNS = (
    "task_id, task_type, topic, status, stage, style, tone, "
    "target_length, retry_count, updated_at"
)


async def _insert_stale_inprogress(conn, task_id: str, *, hours_old: int = 2) -> None:
    await conn.execute("DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)
    await conn.execute(
        f"""
        INSERT INTO pipeline_tasks ({_COLUMNS})
        VALUES ($1, 'blog_post', $2, 'in_progress', 'in_progress',
                'technical', 'professional', 900, 0,
                NOW() - ($3 || ' hours')::interval)
        """,
        task_id,
        "integration_db smoke: sweep vs heartbeat",
        str(hours_old),
    )


async def test_heartbeat_refreshed_task_survives_sweep(test_pool) -> None:
    """A task heartbeat-refreshed AFTER its stale-by-clock claim must not be
    touched by sweep_stale_tasks, even though its original claim looks old."""
    from services.tasks_db import TasksDatabase

    task_id = "sweep-vs-heartbeat-alive-757"
    async with test_pool.acquire() as setup:
        await _insert_stale_inprogress(setup, task_id, hours_old=2)

    try:
        db = TasksDatabase(pool=test_pool)
        # The sidecar heartbeat firing "just now" — updated_at jumps to NOW().
        beat_ok = await db.heartbeat_task(task_id)
        assert beat_ok is True, "heartbeat_task should have updated the row"

        result = await db.sweep_stale_tasks(stale_threshold_minutes=60, max_retries=3)
        assert result["reset"] == 0 and result["failed"] == 0, (
            f"heartbeat-refreshed task must not be swept: {result}"
        )

        async with test_pool.acquire() as verify:
            status = await verify.fetchval(
                "SELECT status FROM pipeline_tasks WHERE task_id = $1", task_id,
            )
        assert status == "in_progress", (
            f"expected the heartbeat-refreshed task to stay in_progress, got {status!r}"
        )
    finally:
        async with test_pool.acquire() as cleanup:
            await cleanup.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = $1", task_id
            )


async def test_task_without_heartbeat_is_still_reclaimed(test_pool) -> None:
    """No-regression guard: a stale in_progress task that never got a
    heartbeat must still be reclaimed exactly as before this change."""
    from services.tasks_db import TasksDatabase

    task_id = "sweep-vs-heartbeat-dead-757"
    async with test_pool.acquire() as setup:
        await _insert_stale_inprogress(setup, task_id, hours_old=2)

    try:
        db = TasksDatabase(pool=test_pool)
        result = await db.sweep_stale_tasks(stale_threshold_minutes=60, max_retries=3)
        assert result["reset"] == 1, (
            f"a never-heartbeated stale task must still be reclaimed: {result}"
        )

        async with test_pool.acquire() as verify:
            row = await verify.fetchrow(
                "SELECT status, retry_count FROM pipeline_tasks WHERE task_id = $1",
                task_id,
            )
        assert row["status"] == "pending"
        assert row["retry_count"] == 1
    finally:
        async with test_pool.acquire() as cleanup:
            await cleanup.execute(
                "DELETE FROM pipeline_tasks WHERE task_id = $1", task_id
            )
