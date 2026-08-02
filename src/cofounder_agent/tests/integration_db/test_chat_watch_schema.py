"""chat_watch SQL vs the REAL schema (twice-bitten: quality_score #2973,
then duration_ms/started_at — unit fakes cannot catch column drift, so
this executes watch_task's actual queries against a migrated database."""
import uuid

import pytest

pytestmark = [
    pytest.mark.integration_db,
    pytest.mark.asyncio(loop_scope="session"),
]


async def test_watch_task_queries_execute_on_real_schema(test_pool):
    from services.chat_watch import watch_task

    task_id = f"watchtest-{uuid.uuid4().hex[:12]}"
    await test_pool.execute(
        """
        INSERT INTO pipeline_tasks (task_id, task_type, topic, status, stage)
        VALUES ($1, 'blog_post', 'watch schema pin', 'in_progress', 'draft')
        """,
        task_id,
    )
    await test_pool.execute(
        """
        INSERT INTO atom_runs (run_id, task_id, atom, node_id, latency_ms, status)
        VALUES ($1, $1, 'content.generate_draft', 'gen_draft', 1234, 'ok')
        """,
        task_id,
    )
    try:
        snap = await watch_task(test_pool, task_id)
        assert snap is not None
        assert snap["task_id"] == task_id
        assert snap["terminal"] is False
        assert snap["nodes_done"] == 1
        assert snap["nodes"][0]["duration_ms"] == 1234
    finally:
        await test_pool.execute(
            "DELETE FROM atom_runs WHERE task_id = $1", task_id)
        await test_pool.execute(
            "DELETE FROM pipeline_tasks WHERE task_id = $1", task_id)
